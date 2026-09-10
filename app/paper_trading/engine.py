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
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repository import PaperRepository

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    """SQLite retourne des datetimes naïfs : ils représentent toujours de
    l'UTC dans ce projet."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


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


# Déclencheur break-even : +1,5R (mi-chemin TP1 -> TP2), même niveau que la
# ligne BE de l'embed de signal (présentation uniquement, jamais un ordre).
BE_TRIGGER_R = Decimal("1.5")


def break_even_level(
    action: str, entry_price: Decimal, stop_loss: Decimal
) -> Decimal:
    """Niveau de prix du déclencheur BE : entry ± 1,5 x risque."""
    risk = (
        entry_price - stop_loss if action == "BUY" else stop_loss - entry_price
    )
    sign = Decimal("1") if action == "BUY" else Decimal("-1")
    return entry_price + sign * BE_TRIGGER_R * risk


def resolve_exit_candle(
    action: str,
    high: Decimal,
    low: Decimal,
    stop_loss: Decimal,
    take_profit: Decimal,
) -> str | None:
    """Détermine la sortie d'une position pour une bougie OHLC complète.

    BUY : SL si low <= stop_loss, TP si high >= take_profit (SL prioritaire si
    les deux sont touchés — hypothèse prudente, fidèle au moteur engine/).
    SELL : inverse.
    """
    if action == "BUY":
        if low <= stop_loss:
            return "SL"
        if high >= take_profit:
            return "TP"
    else:  # SELL
        if high >= stop_loss:
            return "SL"
        if low <= take_profit:
            return "TP"
    return None


@dataclass(frozen=True)
class CloseOutcome:
    """Une position clôturée par le moteur."""

    position_id: int
    signal_id: int
    symbol: str
    strategy: str
    action: str
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    risk_reward: Decimal
    exit_reason: str
    exit_price: Decimal
    result_r: Decimal
    opened_at: datetime
    closed_at: datetime
    sequence_number: int | None = None


@dataclass(frozen=True)
class BeAlert:
    """Déclencheur break-even atteint par le prix (notification humaine).

    Pure information : le paper trading garde son bracket SL/TP d'origine,
    la gestion BE reste une décision humaine (règle absolue du projet).
    """

    position_id: int
    sequence_number: int | None
    symbol: str
    action: str
    entry_price: Decimal
    be_trigger: Decimal


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

    async def check_candle(
        self,
        *,
        symbol: str,
        high: Decimal,
        low: Decimal,
        candle_start: datetime,
    ) -> list[CloseOutcome]:
        """Vérifie les positions ouvertes du symbole contre une bougie fermée.

        Anti-lookahead : une position n'est jamais vérifiée contre la bougie
        qui l'a créée — le timestamp du signal est la borne de clôture de sa
        bougie, donc seules les positions dont signal_timestamp <= début de la
        bougie reçue sont concernées (la bougie SUIVANTE au plus tôt).
        """
        async with self._session_factory() as session:
            repository = PaperRepository(session)
            outcomes: list[CloseOutcome] = []
            for position, signal, strategy_name in await repository.open_with_signal_by_symbol(symbol):
                if _as_utc(signal.signal_timestamp) > candle_start:
                    continue  # position ouverte à la clôture de cette même bougie
                exit_reason = resolve_exit_candle(
                    signal.action, high, low, signal.stop_loss, signal.take_profit
                )
                if exit_reason is None:
                    continue
                exit_price = (
                    signal.stop_loss if exit_reason == "SL" else signal.take_profit
                )
                outcomes.append(
                    await self._close(
                        repository, position, signal, strategy_name, exit_reason, exit_price
                    )
                )
            await session.commit()
        return outcomes

    async def check_break_even(
        self,
        *,
        symbol: str,
        high: Decimal,
        low: Decimal,
        candle_start: datetime,
    ) -> list[BeAlert]:
        """Détecte le déclencheur BE (+1,5R) atteint par une bougie fermée.

        Une position ne déclenche qu'UNE SEULE alerte (be_notified), même si
        le prix repasse le niveau ensuite. Même garde anti-lookahead que
        `check_candle` : jamais la bougie qui a créé la position. Les
        positions clôturées (TP/SL) sont exclues d'office : si la bougie
        touche à la fois le SL et le niveau BE, la clôture SL (prudente)
        l'emporte et aucune alerte BE n'est émise.
        """
        alerts: list[BeAlert] = []
        async with self._session_factory() as session:
            repository = PaperRepository(session)
            for position, signal, _strategy_name in await repository.open_with_signal_by_symbol(symbol):
                if position.be_notified:
                    continue
                if _as_utc(signal.signal_timestamp) > candle_start:
                    continue  # position ouverte à la clôture de cette même bougie
                be_trigger = break_even_level(
                    signal.action, signal.entry_price, signal.stop_loss
                )
                if signal.action == "BUY":
                    touche = high >= be_trigger
                else:
                    touche = low <= be_trigger
                if not touche:
                    continue
                position.be_notified = True
                alerts.append(
                    BeAlert(
                        position_id=position.id,
                        sequence_number=signal.sequence_number,
                        symbol=signal.symbol,
                        action=signal.action,
                        entry_price=signal.entry_price,
                        be_trigger=be_trigger,
                    )
                )
                logger.info(
                    "Déclencheur BE atteint position_id=%s symbol=%s niveau=%s",
                    position.id,
                    signal.symbol,
                    be_trigger,
                )
            await session.commit()
        return alerts

    async def _close(
        self,
        repository: PaperRepository,
        position,
        signal,
        strategy_name: str,
        exit_reason: str,
        exit_price: Decimal,
    ) -> CloseOutcome:
        result_r = result_in_r(exit_reason, signal.risk_reward)
        await repository.close_position(
            position, exit_reason=exit_reason, exit_price=exit_price, result_r=result_r
        )
        logger.info(
            "Position paper clôturée id=%s symbol=%s sortie=%s résultat=%sR",
            position.id,
            signal.symbol,
            exit_reason,
            result_r,
        )
        return CloseOutcome(
            position_id=position.id,
            signal_id=signal.id,
            symbol=signal.symbol,
            strategy=strategy_name,
            action=signal.action,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            risk_reward=signal.risk_reward,
            exit_reason=exit_reason,
            exit_price=exit_price,
            result_r=result_r,
            opened_at=_as_utc(position.opened_at),
            closed_at=_as_utc(position.closed_at),
            sequence_number=signal.sequence_number,
        )

    async def _check_symbol(
        self, repository: PaperRepository, symbol: str, price: Decimal
    ) -> list[CloseOutcome]:
        """Clôture toute position ouverte du symbole dont le SL ou le TP est
        atteint par ce prix."""
        outcomes: list[CloseOutcome] = []
        for position, signal, strategy_name in await repository.open_with_signal_by_symbol(symbol):
            exit_reason = resolve_exit(
                signal.action,
                price,
                signal.stop_loss,
                signal.take_profit,
            )
            if exit_reason is None:
                continue
            outcomes.append(
                await self._close(
                    repository, position, signal, strategy_name, exit_reason, price
                )
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
