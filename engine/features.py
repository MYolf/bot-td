"""Features de qualification par bougie : tendance, momentum, volatilité,
volume, VWAP.

RÔLE EXCLU PAR CONCEPTION (voir CONFLUENCE.md) : ces features ne déclenchent
JAMAIS un signal. Elles qualifient un signal né d'un événement structurel
(``engine.structure``) : gâchettes de contexte, score de confluence,
dimensionnement SL/TP.

Fusion anti-corrélation : chaque famille produit UNE valeur d'état catégoriel
(bullish / bearish / neutral) — les indicateurs d'une même famille (RSI+MACD,
EMA+pente+distance) sont fusionnés et ne comptent jamais double.

Contrat anti-lookahead (propriété de préfixe, testée en test unitaire) :
``feature(candles[:t+1]) == feature(candles)[:t+1]`` pour toute feature.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.indicators import atr, ema, macd, rsi, sma
from engine.strategy import Candle

BULLISH = "bullish"
BEARISH = "bearish"
NEUTRAL = "neutral"

DAY_MS = 86_400_000


# ---------------------------------------------------------------- tendance --


@dataclass(frozen=True)
class TrendState:
    """État de tendance 15m (UNE valeur fusionnée, jamais un déclencheur)."""

    bias: str  # bullish / bearish / neutral (neutral = amorce non finie)
    separation: float | None  # |ema_fast / ema_slow - 1|
    slope: float | None  # pente de l'EMA rapide sur ``slope_bars`` (relatif)
    price_above_fast: bool | None  # clôture au-dessus de l'EMA rapide


def trend_states(
    candles: list[Candle],
    ema_fast_len: int = 50,
    ema_slow_len: int = 200,
    slope_bars: int = 5,
) -> list[TrendState]:
    closes = [c.close for c in candles]
    ema_fast = ema(closes, ema_fast_len)
    ema_slow = ema(closes, ema_slow_len)
    out: list[TrendState] = []
    for i, close in enumerate(closes):
        fast = ema_fast[i]
        slow = ema_slow[i]
        if fast is None or slow is None:
            out.append(TrendState(NEUTRAL, None, None, None))
            continue
        slope: float | None = None
        previous = ema_fast[i - slope_bars] if i >= slope_bars else None
        if previous is not None and fast != 0.0:
            slope = (fast - previous) / fast
        out.append(
            TrendState(
                bias=BULLISH if fast > slow else BEARISH,
                separation=abs(fast / slow - 1.0),
                slope=slope,
                price_above_fast=close > fast,
            )
        )
    return out


# ---------------------------------------------------------------- momentum --


@dataclass(frozen=True)
class MomentumState:
    """Momentum FUSIONNÉ RSI + MACD : une seule valeur d'état plafonnée."""

    bias: str
    rsi: float | None
    macd_hist: float | None
    hist_expanding: bool | None  # |hist| en expansion vs bougie précédente


def momentum_states(
    candles: list[Candle],
    rsi_len: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
) -> list[MomentumState]:
    closes = [c.close for c in candles]
    rsi_values = rsi(closes, rsi_len)
    macd_line, macd_signal_line, macd_hist = macd(
        closes, macd_fast, macd_slow, macd_signal
    )
    out: list[MomentumState] = []
    for i in range(len(candles)):
        r = rsi_values[i]
        h = macd_hist[i]
        line_up = (
            macd_line[i] is not None
            and macd_signal_line[i] is not None
            and macd_line[i] > macd_signal_line[i]
        )
        line_down = (
            macd_line[i] is not None
            and macd_signal_line[i] is not None
            and macd_line[i] < macd_signal_line[i]
        )
        # Fusion : il faut l'accord des DEUX familles (RSI côté 50, MACD côté
        # ligne/signal) pour un état directionnel ; sinon neutral.
        if r is not None and r > 50.0 and line_up:
            bias = BULLISH
        elif r is not None and r < 50.0 and line_down:
            bias = BEARISH
        else:
            bias = NEUTRAL
        expanding: bool | None = None
        if h is not None and i >= 1 and macd_hist[i - 1] is not None:
            expanding = abs(h) > abs(macd_hist[i - 1])
        out.append(MomentumState(bias=bias, rsi=r, macd_hist=h, hist_expanding=expanding))
    return out


# -------------------------------------------------------------- volatilité --


@dataclass(frozen=True)
class VolatilityState:
    """Volatilité : sert aux gâchettes et au dimensionnement, pas de sens."""

    atr: float | None
    atr_pct: float | None  # ATR relatif au prix
    expansion: float | None  # atr[i] / moyenne des dernières valeurs - 1


def volatility_states(
    candles: list[Candle],
    atr_len: int = 14,
    ref_bars: int = 20,
) -> list[VolatilityState]:
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    atrs = atr(highs, lows, closes, atr_len)
    out: list[VolatilityState] = []
    for i, close in enumerate(closes):
        a = atrs[i]
        if a is None or close == 0.0:
            out.append(VolatilityState(None, None, None))
            continue
        expansion: float | None = None
        window = [v for v in atrs[max(0, i - ref_bars + 1) : i + 1] if v is not None]
        if window:
            expansion = a / (sum(window) / len(window)) - 1.0
        out.append(VolatilityState(atr=a, atr_pct=a / close, expansion=expansion))
    return out


# ------------------------------------------------------------------ volume --


@dataclass(frozen=True)
class VolumeState:
    """Volume relatif (RVOL) : confirmation uniquement, seuils par backtest."""

    rvol: float | None  # volume / SMA des avg_len bougies PRÉCÉDENTES


def volume_states(candles: list[Candle], avg_len: int = 20) -> list[VolumeState]:
    volumes = [c.volume for c in candles]
    average = sma(volumes, avg_len)
    out: list[VolumeState] = []
    for i, volume in enumerate(volumes):
        # Moyenne des bougies précédentes uniquement (pas la courante) :
        # sinon un pic de volume se dilue dans sa propre référence.
        avg = average[i - 1] if i >= 1 else None
        rvol = volume / avg if avg is not None and avg > 0.0 else None
        out.append(VolumeState(rvol=rvol))
    return out


# ------------------------------------------------------------------- vwap ---


def vwap_daily(candles: list[Candle]) -> list[float | None]:
    """VWAP ancré à 00:00 UTC (reset à chaque changement de jour).

    Crypto 24/7 : il n'y a pas d'open de session ; l'ancre jour UTC est le
    standard le plus simple et reproductible. Prix typique (h+l+c)/3 comme
    Pine. Contexte secondaire — jamais un déclencheur.
    """
    out: list[float | None] = []
    current_day: int | None = None
    cum_pv = 0.0
    cum_volume = 0.0
    for candle in candles:
        day = candle.open_time // DAY_MS
        if day != current_day:
            current_day = day
            cum_pv = 0.0
            cum_volume = 0.0
        typical = (candle.high + candle.low + candle.close) / 3.0
        cum_pv += typical * candle.volume
        cum_volume += candle.volume
        out.append(cum_pv / cum_volume if cum_volume > 0.0 else None)
    return out
