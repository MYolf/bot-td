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

from app.database.models import Signal, Strategy
from app.signals.deduplication import build_signal_uid
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

    async def get_latest(self, limit: int = 10) -> list[tuple[Signal, str]]:
        """Derniers signaux reçus, du plus récent au plus ancien.

        Retourne (signal, nom de la stratégie) pour l'affichage.
        """
        stmt = (
            select(Signal, Strategy.name)
            .join(Strategy, Signal.strategy_id == Strategy.id)
            .order_by(Signal.received_at.desc(), Signal.id.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(signal, name) for signal, name in rows]

    async def count_all(self) -> int:
        """Nombre total de signaux stockés."""
        return await self._session.scalar(select(func.count()).select_from(Signal)) or 0

    async def count_by_status(self) -> dict[str, int]:
        """Effectifs par statut (SENT, ERROR, ...)."""
        rows = (
            await self._session.execute(
                select(Signal.status, func.count()).group_by(Signal.status)
            )
        ).all()
        return {status: count for status, count in rows}

    async def count_by_action(self) -> dict[str, int]:
        """Effectifs par action (BUY/SELL)."""
        rows = (
            await self._session.execute(
                select(Signal.action, func.count()).group_by(Signal.action)
            )
        ).all()
        return {action: count for action, count in rows}

    async def count_by_strategy(self) -> dict[str, int]:
        """Effectifs par nom de stratégie."""
        rows = (
            await self._session.execute(
                select(Strategy.name, func.count(Signal.id))
                .join(Signal, Signal.strategy_id == Strategy.id)
                .group_by(Strategy.name)
            )
        ).all()
        return {name: count for name, count in rows}
