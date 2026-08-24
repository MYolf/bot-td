"""Moteur de paper trading (Phase 21) — simulation locale, jamais d'ordre réel.

Fonctionnement (Projet.md §31-33) : chaque signal stocké ouvre une position
virtuelle (entry/SL/TP du signal). Le prix d'entrée de tout signal reçu
ultérieurement sur le même symbole sert de prix de marché : si ce prix atteint
le SL ou le TP d'une position ouverte, elle est clôturée et le résultat est
enregistré en R (TP atteint -> +RR du signal ; SL atteint -> -1R).

La simulation vit uniquement des signaux et de la base de données : AUCUN
appel réseau vers un broker ou un exchange (règle absolue du projet).
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repository import PaperRepository

logger = logging.getLogger(__name__)


def resolve_exit(
    action: str,
    price: Decimal,
    stop_loss: Decimal,
    take_profit: Decimal,
) -> str | None:
    """Détermine la sortie d'une position pour un prix donné.

    BUY : SL si prix <= stop_loss, TP si prix >= take_profit.
    SELL : inverse. Aucune sortie -> None. (La cohérence SL/entry/TP est
    garantie par la validation du signal, donc un prix ne peut pas toucher
    les deux à la fois.)
    """
    if action == "BUY":
        if price <= stop_loss:
            return "SL"
        if price >= take_profit:
            return "TP"
    else:  # SELL
        if price >= stop_loss:
            return "SL"
        if price <= take_profit:
            return "TP"
    return None


def result_in_r(exit_reason: str, risk_reward: Decimal) -> Decimal:
    """Résultat en R (Projet.md §33) : TP -> +RR, SL -> -1R."""
    if exit_reason == "TP":
        return risk_reward
    return Decimal("-1")


@dataclass(frozen=True)
class CloseOutcome:
    """Une position clôturée par le moteur."""

    position_id: int
    symbol: str
    exit_reason: str
    result_r: Decimal


class PaperTradingEngine:
    """Ouvre et clôture des positions virtuelles à partir des signaux stockés."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def process_signal(
        self,
        *,
        signal_id: int,
        symbol: str,
        action: str,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
    ) -> list[CloseOutcome]:
        """Traite un signal qui vient d'être stocké : ouvre sa position
        virtuelle, puis vérifie les positions ouvertes du symbole avec son
        prix d'entrée comme prix de marché.

        Retourne les positions clôturées à cette occasion.
        """
        async with self._session_factory() as session:
            repository = PaperRepository(session)
            position = await repository.open_for_signal(signal_id=signal_id)
            if position is None:
                logger.info(
                    "Position paper déjà existante signal_id=%s (ignoré)", signal_id
                )
            else:
                logger.info(
                    "Position paper ouverte id=%s signal_id=%s symbol=%s action=%s",
                    position.id,
                    signal_id,
                    symbol,
                    action,
                )
            closed = await self._check_symbol(repository, symbol, entry_price)
            await session.commit()
        return closed

    async def _check_symbol(
        self, repository: PaperRepository, symbol: str, price: Decimal
    ) -> list[CloseOutcome]:
        """Clôture toute position ouverte du symbole dont le SL ou le TP est
        atteint par ce prix."""
        outcomes: list[CloseOutcome] = []
        for position, signal in await repository.open_with_signal_by_symbol(symbol):
            exit_reason = resolve_exit(
                signal.action,
                price,
                signal.stop_loss,
                signal.take_profit,
            )
            if exit_reason is None:
                continue
            result_r = result_in_r(exit_reason, signal.risk_reward)
            await repository.close_position(
                position, exit_reason=exit_reason, exit_price=price, result_r=result_r
            )
            outcomes.append(
                CloseOutcome(
                    position_id=position.id,
                    symbol=symbol,
                    exit_reason=exit_reason,
                    result_r=result_r,
                )
            )
            logger.info(
                "Position paper clôturée id=%s symbol=%s sortie=%s résultat=%sR",
                position.id,
                symbol,
                exit_reason,
                result_r,
            )
        return outcomes


# --- Instance globale (même principe que le notifieur Discord) ---

_engine: PaperTradingEngine | None = None


def init_paper_engine(session_factory: async_sessionmaker[AsyncSession]) -> None:
    global _engine
    _engine = PaperTradingEngine(session_factory)
    logger.info("Moteur de paper trading initialisé")


def shutdown_paper_engine() -> None:
    global _engine
    _engine = None
    logger.info("Moteur de paper trading arrêté")


def get_paper_engine() -> PaperTradingEngine | None:
    return _engine


def provide_paper_engine() -> PaperTradingEngine | None:
    """Dépendance FastAPI : moteur courant (None si non initialisé)."""
    return get_paper_engine()
