"""Repositories d'accès aux données (Phase 11).

Toutes les requêtes SQL vivent ici : le Signal Engine, le webhook et le bot
Discord ne manipulent jamais de sessions/Query directement (testable avec
une base de test ou des mocks).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
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
