"""Validation métier des signaux (Phases 8 et 9).

Deux niveaux de contrôle :
- listes blanches configurables (stratégie, symbole, exchange, timeframe) ;
- cohérence des prix (BUY : SL < entrée < TP ; SELL : TP < entrée < SL) ;
- fraîcheur du timestamp.

Chaque rejet renvoie un code de raison (jamais de secret, jamais de détail
sensible) et est loggé.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config.settings import Settings
from app.signals.schemas import TradingViewSignal

logger = logging.getLogger(__name__)

# Tolérance acceptée pour un timestamp légèrement dans le futur (horloges désynchronisées).
FUTURE_TOLERANCE_SECONDS = 60


@dataclass(frozen=True)
class ValidationResult:
    """Résultat de la validation métier d'un signal."""

    valid: bool
    reason: str | None = None


def _as_utc(value: datetime) -> datetime:
    """Considère un datetime naïf comme UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_signal(signal: TradingViewSignal, settings: Settings) -> ValidationResult:
    """Valide un signal contre la configuration. Retourne valid=False + code de raison."""

    # --- Phase 8 : listes blanches configurables ---
    if signal.strategy not in settings.allowed_strategies:
        return ValidationResult(False, "invalid_strategy")
    if signal.symbol not in settings.allowed_symbols:
        return ValidationResult(False, "invalid_symbol")
    if signal.exchange not in settings.allowed_exchanges:
        return ValidationResult(False, "invalid_exchange")
    if signal.timeframe not in settings.allowed_timeframes:
        return ValidationResult(False, "invalid_timeframe")

    # --- Phase 9 : cohérence des prix ---
    if signal.action == "BUY":
        if not signal.stop_loss < signal.price:
            return ValidationResult(False, "incoherent_stop_loss")
        if not signal.take_profit > signal.price:
            return ValidationResult(False, "incoherent_take_profit")
    else:  # SELL
        if not signal.stop_loss > signal.price:
            return ValidationResult(False, "incoherent_stop_loss")
        if not signal.take_profit < signal.price:
            return ValidationResult(False, "incoherent_take_profit")

    # --- Phase 8 : fraîcheur du timestamp ---
    now = datetime.now(timezone.utc)
    signal_time = _as_utc(signal.timestamp)
    if now - signal_time > timedelta(seconds=settings.signal_max_age_seconds):
        return ValidationResult(False, "expired_timestamp")
    if signal_time - now > timedelta(seconds=FUTURE_TOLERANCE_SECONDS):
        return ValidationResult(False, "future_timestamp")

    return ValidationResult(True)
