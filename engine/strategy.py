"""Portage Python exact de la stratégie Pine « Momentum V1 ».

Source de vérité : ``pine/momentum_v1.pine``. Toute modification ici doit
être répercutée dans le Pine (et inversement).

Règles (identiques au Pine) :
- BUY  : EMA50 > EMA200 ET RSI > seuil haut ET MACD bullish ;
- SELL : EMA50 < EMA200 ET RSI < seuil bas  ET MACD bearish ;
- signal à la TRANSITION uniquement (conditions vraies maintenant mais pas
  à la bougie précédente) ;
- SL/TP en pourcentage de l'entrée (close de la bougie fermée) ;
- scores de qualité Phase 26 : trend /20, momentum /20, MACD /15.

Anti-repainting : ``evaluate_momentum_v1`` ne doit recevoir que des bougies
FERMÉES (la bougie en cours ne doit jamais figurer dans ``candles``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from engine.indicators import ema, macd, rsi

logger = logging.getLogger(__name__)


def _gt(a: float | None, b: float | None) -> bool:
    """a > b avec la sémantique Pine : une comparaison avec na est fausse."""
    return a is not None and b is not None and a > b


def _lt(a: float | None, b: float | None) -> bool:
    """a < b avec la sémantique Pine : une comparaison avec na est fausse."""
    return a is not None and b is not None and a < b


STRATEGY_NAME = "momentum_v1"
EXCHANGE = "BINANCE"  # source Binance : cohérent avec la liste blanche backend


@dataclass(frozen=True)
class Candle:
    """Bougie OHLCV (temps en millisecondes Unix, like Binance klines)."""

    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class MomentumParams:
    """Paramètres de Momentum V1 (identiques aux inputs du Pine)."""

    ema_fast: int = 50
    ema_slow: int = 200
    rsi_len: int = 14
    rsi_long_threshold: float = 55.0
    rsi_short_threshold: float = 45.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    sl_pct: float = 0.01  # 1 %
    tp_pct: float = 0.02  # 2 %


@dataclass(frozen=True)
class SignalResult:
    """Signal calculé par la stratégie (une transition détectée)."""

    action: str  # "BUY" ou "SELL"
    entry: float
    stop_loss: float
    take_profit: float
    score_trend: int
    score_momentum: int
    score_macd: int
    candle_open_time: int  # ms
    candle_close_time: int  # ms, borne incluse -> timestamp du signal


def evaluate_momentum_v1(
    candles: list[Candle], params: MomentumParams
) -> SignalResult | None:
    """Évalue la transition sur la DERNIÈRE bougie fermée de ``candles``.

    Retourne None : pas de transition, ou pas assez d'historique
    (EMA200 + amorce MACD + bougie précédente).
    """
    closes = [c.close for c in candles]
    # Amorce maximale : EMA lente (200) ; il faut en plus la bougie
    # précédente (transition) et l'amorce du signal MACD (9 après slow-1).
    min_len = params.ema_slow + params.macd_signal + 2
    if len(closes) < min_len:
        logger.warning(
            "Historique insuffisant : %d bougies, minimum %d", len(closes), min_len
        )
        return None

    ema_fast = ema(closes, params.ema_fast)
    ema_slow = ema(closes, params.ema_slow)
    rsi_values = rsi(closes, params.rsi_len)
    macd_line, macd_signal_line, macd_hist = macd(
        closes, params.macd_fast, params.macd_slow, params.macd_signal
    )

    i = len(closes) - 1  # dernière bougie fermée
    j = i - 1  # bougie précédente (pour la transition)

    # Comparaisons « na-safe » : en Pine, une comparaison avec na est fausse
    # (indicateur non défini => condition fausse, pas d'abandon).
    trend_up = _gt(ema_fast[i], ema_slow[i])
    trend_down = _lt(ema_fast[i], ema_slow[i])
    macd_bullish = _gt(macd_line[i], macd_signal_line[i])
    macd_bearish = _lt(macd_line[i], macd_signal_line[i])

    all_bullish_now = (
        trend_up and _gt(rsi_values[i], params.rsi_long_threshold) and macd_bullish
    )
    all_bearish_now = (
        trend_down and _lt(rsi_values[i], params.rsi_short_threshold) and macd_bearish
    )
    all_bullish_prev = (
        _gt(ema_fast[j], ema_slow[j])
        and _gt(rsi_values[j], params.rsi_long_threshold)
        and _gt(macd_line[j], macd_signal_line[j])
    )
    all_bearish_prev = (
        _lt(ema_fast[j], ema_slow[j])
        and _lt(rsi_values[j], params.rsi_short_threshold)
        and _lt(macd_line[j], macd_signal_line[j])
    )

    if all_bullish_now and not all_bullish_prev:
        action = "BUY"
    elif all_bearish_now and not all_bearish_prev:
        action = "SELL"
    else:
        return None

    entry = closes[i]
    if action == "BUY":
        stop_loss = entry * (1 - params.sl_pct)
        take_profit = entry * (1 + params.tp_pct)
    else:
        stop_loss = entry * (1 + params.sl_pct)
        take_profit = entry * (1 - params.tp_pct)

    # --- Scores Phase 26 (barème identique au Pine) ---
    trend_sep = abs(ema_fast[i] / ema_slow[i] - 1)
    score_trend = 20 if (trend_up or trend_down) and trend_sep > 0.02 else 10
    rsi_now = rsi_values[i]
    rsi_force = abs(rsi_now - 50) if rsi_now is not None else 0.0
    score_momentum = 20 if rsi_force >= 15 else 10
    # macdExpand : histogramme en expansion par rapport à la bougie précédente.
    hist_now = macd_hist[i] if macd_hist[i] is not None else 0.0
    hist_prev = macd_hist[j] if macd_hist[j] is not None else 0.0
    score_macd = 15 if abs(hist_now) > abs(hist_prev) else 8

    candle = candles[i]
    # La borne de clôture (open + durée) sert de timestamp : stable pour la
    # déduplication, et fraîche (< SIGNAL_MAX_AGE_SECONDS) car l'émission a
    # lieu juste après la clôture réelle.
    close_boundary = candle.open_time + (candle.close_time - candle.open_time + 1)

    return SignalResult(
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        score_trend=score_trend,
        score_momentum=score_momentum,
        score_macd=score_macd,
        candle_open_time=candle.open_time,
        candle_close_time=close_boundary,
    )


def build_payload(
    result: SignalResult,
    symbol: str,
    timeframe: str,
    secret: str,
) -> dict:
    """Construit le JSON du webhook (schéma exact de TradingViewSignal)."""
    timestamp = datetime.fromtimestamp(result.candle_close_time / 1000, tz=timezone.utc)
    return {
        "secret": secret,
        "strategy": STRATEGY_NAME,
        "symbol": symbol,
        "exchange": EXCHANGE,
        "timeframe": timeframe,
        "action": result.action,
        "price": result.entry,
        "stop_loss": result.stop_loss,
        "take_profit": result.take_profit,
        "score_trend": result.score_trend,
        "score_momentum": result.score_momentum,
        "score_macd": result.score_macd,
        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
