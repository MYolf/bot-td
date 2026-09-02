"""Repositories d'accès aux données (Phase 11).

Toutes les requêtes SQL vivent ici : le Signal Engine, le webhook et le bot
Discord ne manipulent jamais de sessions/Query directement (testable avec
une base de test ou des mocks).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PaperPosition, PaperTrade, Signal, Strategy
from app.signals.deduplication import build_signal_uid
from app.signals.scoring import compute_score
from app.signals.schemas import TradingViewSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InsertResult:
    """Résultat de l'insertion d'un signal validé."""

    duplicate: bool
    signal_id: int | None
    signal_uid: str


def compute_risk_reward(signal: TradingViewSignal) -> Decimal:
    """Risk/Reward calculé côté backend (Projet.md §20)."""
    entry = Decimal(str(signal.price))
    sl = Decimal(str(signal.stop_loss))
    tp = Decimal(str(signal.take_profit))
    if signal.action == "BUY":
        risk = entry - sl
        reward = tp - entry
    else:  # SELL
        risk = sl - entry
        reward = entry - tp
    return (reward / risk).quantize(Decimal("0.0001"))


class StrategyRepository:
    """Accès à la table strategies."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_or_create(self, name: str) -> Strategy:
        """Retourne la stratégie, la crée à la volée si inconnue.

        L'historique est immuable : une stratégie obsolète est désactivée
        (enabled=false), jamais supprimée.
        """
        strategy = await self._session.scalar(
            select(Strategy).where(Strategy.name == name)
        )
        if strategy is None:
            strategy = Strategy(name=name)
            self._session.add(strategy)
            await self._session.flush()
            logger.info("Stratégie créée name=%s", name)
        return strategy

    async def list_all(self) -> list[Strategy]:
        """Toutes les stratégies (commande /strategy, Phase 20)."""
        result = await self._session.scalars(
            select(Strategy).order_by(Strategy.name)
        )
        return list(result)


class SignalRepository:
    """Accès à la table signals."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def insert_validated(
        self,
        signal: TradingViewSignal,
        strategy_id: int,
    ) -> InsertResult:
        """Insère un signal validé. Un conflit signal_uid => chemin DUPLICATE.

        La session est commitée par `session_scope` si aucune exception ne
        remonte ; en cas de doublon, rollback puis statut DUPLICATE (le
        doublon est ignoré : jamais deux messages Discord).
        """
        signal_uid = build_signal_uid(
            strategy=signal.strategy,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            candle_timestamp=signal.timestamp,
            action=signal.action,
        )
        row = Signal(
            signal_uid=signal_uid,
            strategy_id=strategy_id,
            symbol=signal.symbol,
            exchange=signal.exchange,
            timeframe=signal.timeframe,
            action=signal.action,
            entry_price=Decimal(str(signal.price)),
            stop_loss=Decimal(str(signal.stop_loss)),
            take_profit=Decimal(str(signal.take_profit)),
            risk_reward=compute_risk_reward(signal),
            score=compute_score(signal),
            signal_timestamp=signal.timestamp,
            received_at=datetime.now(timezone.utc),
            status="VALIDATED",
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            logger.info("Signal dupliqué ignoré signal_uid=%s", signal_uid)
            return InsertResult(duplicate=True, signal_id=None, signal_uid=signal_uid)
        return InsertResult(duplicate=False, signal_id=row.id, signal_uid=signal_uid)

    async def mark_sent(self, signal_id: int, discord_message_id: int) -> None:
        """Passe le signal à SENT et stocke l'identifiant du message Discord."""
        await self._session.execute(
            sa_update(Signal)
            .where(Signal.id == signal_id)
            .values(status="SENT", discord_message_id=discord_message_id)
        )

    async def mark_error(self, signal_id: int) -> None:
        """Passe le signal à ERROR (échec de notification, signal conservé)."""
        await self._session.execute(
            sa_update(Signal).where(Signal.id == signal_id).values(status="ERROR")
        )

    async def get(self, signal_id: int) -> Signal | None:
        """Charge un signal par id (commandes Discord, re-notification)."""
        return await self._session.get(Signal, signal_id)

    # --- Phase 20 : lectures pour les commandes Discord ---
    # --- Phase 23 : filtres optionnels timeframe / stratégie ---

    def _filtered(
        self,
        stmt,
        *,
        timeframe: str | None = None,
        strategy: str | None = None,
    ):
        """Applique les filtres optionnels (multi-timeframe / multi-stratégie)."""
        if strategy is not None:
            stmt = stmt.join(Strategy, Signal.strategy_id == Strategy.id).where(
                Strategy.name == strategy
            )
        if timeframe is not None:
            stmt = stmt.where(Signal.timeframe == timeframe)
        return stmt

    async def get_latest(
        self,
        limit: int = 10,
        *,
        timeframe: str | None = None,
        strategy: str | None = None,
    ) -> list[tuple[Signal, str]]:
        """Derniers signaux reçus, du plus récent au plus ancien.

        Retourne (signal, nom de la stratégie) pour l'affichage.
        """
        stmt = self._filtered(
            select(Signal, Strategy.name).join(
                Strategy, Signal.strategy_id == Strategy.id
            ),
            timeframe=timeframe,
            strategy=strategy,
        ).order_by(Signal.received_at.desc(), Signal.id.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).all()
        return [(signal, name) for signal, name in rows]

    async def count_all(
        self,
        *,
        timeframe: str | None = None,
        strategy: str | None = None,
    ) -> int:
        """Nombre total de signaux stockés (filtrable)."""
        stmt = self._filtered(
            select(func.count()).select_from(Signal),
            timeframe=timeframe,
            strategy=strategy,
        )
        return await self._session.scalar(stmt) or 0

    async def count_by_status(
        self,
        *,
        timeframe: str | None = None,
        strategy: str | None = None,
    ) -> dict[str, int]:
        """Effectifs par statut (SENT, ERROR, ...), filtrable."""
        stmt = self._filtered(
            select(Signal.status, func.count()),
            timeframe=timeframe,
            strategy=strategy,
        ).group_by(Signal.status)
        rows = (await self._session.execute(stmt)).all()
        return {status: count for status, count in rows}

    async def count_errors_since(self, since: datetime) -> int:
        """Signaux ERROR reçus depuis `since` (alertes de santé)."""
        stmt = (
            select(func.count())
            .select_from(Signal)
            .where(Signal.status == "ERROR", Signal.received_at >= since)
        )
        return await self._session.scalar(stmt) or 0

    async def count_by_action(
        self,
        *,
        timeframe: str | None = None,
        strategy: str | None = None,
    ) -> dict[str, int]:
        """Effectifs par action (BUY/SELL), filtrable."""
        stmt = self._filtered(
            select(Signal.action, func.count()),
            timeframe=timeframe,
            strategy=strategy,
        ).group_by(Signal.action)
        rows = (await self._session.execute(stmt)).all()
        return {action: count for action, count in rows}

    async def count_by_strategy(
        self,
        *,
        timeframe: str | None = None,
    ) -> dict[str, int]:
        """Effectifs par nom de stratégie, filtrable par timeframe."""
        stmt = select(Strategy.name, func.count(Signal.id)).join(
            Signal, Signal.strategy_id == Strategy.id
        )
        if timeframe is not None:
            stmt = stmt.where(Signal.timeframe == timeframe)
        rows = (await self._session.execute(stmt.group_by(Strategy.name))).all()
        return {name: count for name, count in rows}


class PaperRepository:
    """Accès aux tables paper_positions / paper_trades (Phase 21)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def open_for_signal(self, *, signal_id: int) -> PaperPosition | None:
        """Ouvre une position virtuelle pour un signal stocké.

        Retourne None si une position existe déjà pour ce signal (garantie
        d'unicité même sans contrainte base : un signal = au plus une position).
        """
        existing = await self._session.scalar(
            select(PaperPosition).where(PaperPosition.signal_id == signal_id)
        )
        if existing is not None:
            return None
        position = PaperPosition(signal_id=signal_id, status="OPEN")
        self._session.add(position)
        await self._session.flush()
        return position

    async def open_with_signal_by_symbol(
        self, symbol: str
    ) -> list[tuple[PaperPosition, Signal, str]]:
        """Positions ouvertes d'un symbole, avec le signal et la stratégie d'origine."""
        stmt = (
            select(PaperPosition, Signal, Strategy.name)
            .join(Signal, PaperPosition.signal_id == Signal.id)
            .join(Strategy, Signal.strategy_id == Strategy.id)
            .where(PaperPosition.status == "OPEN", Signal.symbol == symbol)
            .order_by(PaperPosition.id)
        )
        return list((await self._session.execute(stmt)).all())

    async def close_position(
        self,
        position: PaperPosition,
        *,
        exit_reason: str,
        exit_price: Decimal,
        result_r: Decimal,
    ) -> None:
        """Clôture une position et enregistre le trade simulé (résultat en R)."""
        position.status = "CLOSED"
        position.closed_at = datetime.now(timezone.utc)
        position.result_r = result_r
        self._session.add(
            PaperTrade(
                paper_position_id=position.id,
                exit_reason=exit_reason,
                exit_price=exit_price,
            )
        )

    async def closed_rows(
        self,
        *,
        timeframe: str | None = None,
        strategy: str | None = None,
    ) -> list[tuple[Decimal, str, str]]:
        """Trades clôturés (result_r, symbol, action), filtrables par
        timeframe/stratégie — sert aux statistiques et ventilations /stats."""
        stmt = (
            select(PaperPosition.result_r, Signal.symbol, Signal.action)
            .join(Signal, PaperPosition.signal_id == Signal.id)
            .where(PaperPosition.status == "CLOSED", PaperPosition.result_r.is_not(None))
        )
        if strategy is not None:
            stmt = stmt.join(Strategy, Signal.strategy_id == Strategy.id).where(
                Strategy.name == strategy
            )
        if timeframe is not None:
            stmt = stmt.where(Signal.timeframe == timeframe)
        stmt = stmt.order_by(PaperPosition.closed_at)
        return [tuple(row) for row in (await self._session.execute(stmt)).all()]

    async def count_open(self) -> int:
        """Nombre de positions actuellement ouvertes."""
        return (
            await self._session.scalar(
                select(func.count())
                .select_from(PaperPosition)
                .where(PaperPosition.status == "OPEN")
            )
            or 0
        )

    # --- Récap quotidien (positions ouvertes/clôturées par période) ---

    async def opened_between(
        self, start: datetime, end: datetime
    ) -> list[tuple[PaperPosition, Signal, str]]:
        """Positions ouvertes entre start et end (bornes UTC), avec signal."""
        stmt = (
            select(PaperPosition, Signal, Strategy.name)
            .join(Signal, PaperPosition.signal_id == Signal.id)
            .join(Strategy, Signal.strategy_id == Strategy.id)
            .where(PaperPosition.opened_at >= start, PaperPosition.opened_at < end)
            .order_by(PaperPosition.opened_at)
        )
        return list((await self._session.execute(stmt)).all())

    async def closed_between(
        self, start: datetime, end: datetime
    ) -> list[tuple[PaperPosition, PaperTrade, Signal, str]]:
        """Positions clôturées entre start et end, avec trade, signal, stratégie."""
        stmt = (
            select(PaperPosition, PaperTrade, Signal, Strategy.name)
            .join(PaperTrade, PaperTrade.paper_position_id == PaperPosition.id)
            .join(Signal, PaperPosition.signal_id == Signal.id)
            .join(Strategy, Signal.strategy_id == Strategy.id)
            .where(PaperPosition.closed_at >= start, PaperPosition.closed_at < end)
            .order_by(PaperPosition.closed_at)
        )
        return list((await self._session.execute(stmt)).all())

    async def open_all(self) -> list[tuple[PaperPosition, Signal, str]]:
        """Toutes les positions ouvertes (tous symboles), avec signal."""
        stmt = (
            select(PaperPosition, Signal, Strategy.name)
            .join(Signal, PaperPosition.signal_id == Signal.id)
            .join(Strategy, Signal.strategy_id == Strategy.id)
            .where(PaperPosition.status == "OPEN")
            .order_by(PaperPosition.opened_at)
        )
        return list((await self._session.execute(stmt)).all())
