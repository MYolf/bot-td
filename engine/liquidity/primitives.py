"""Primitives LIQUIDITY.md §4 — pools, range, FVG de réversion (4H/1H).

Toutes les fonctions respectent la propriété de préfixe (anti-lookahead) :
la valeur à l'index ``t`` n'utilise que des bougies d'index <= ``t``. Les
swings fractals et l'ATR/EMA viennent des modules déjà testés du moteur
(``engine.structure``, ``engine.indicators``).

Conventions de temps : clôture LOGIQUE d'une bougie = ``open_time + durée``
(les ``close_time`` Binance valent ``open_time + durée - 1 ms`` et prêtent
à confusion). Les bougies 4H ont ``open_time`` multiple de 4 h, les 1H de
1 h, origine commune.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from engine.indicators import atr as atr_series
from engine.indicators import ema as ema_series
from engine.structure import SwingPoint, find_swings
from engine.strategy import Candle

# --- Paramètres figés (LIQUIDITY.md §11) --------------------------------

SWING_K = 3  # fractale : 3 bougies strictes de chaque côté
POOL_WINDOW = 50  # fenêtre glissante des pools, en bougies 4H
RANGE_MIN_ATR = 5.0  # largeur minimale du range, en multiples d'ATR14 4H
SWEEP_BUFFER_ATR = 0.05  # pénétration minimale du sweep, en ATR14 4H
SL_BUFFER_ATR = 0.25  # tampon du SL derrière la structure, en ATR14 4H
CONSUME_ATR = 0.25  # anti-re-sweep : niveau consommé à ± cet écart d'ATR
CANCEL_ATR = 0.5  # annulation d'ordre : clôture 4H au-delà du pool
RETEST_ATR = 0.1  # tolérance du retest (candidate C), en ATR14 4H
DISPLACEMENT_ATR_1H = 1.5  # corps minimal du displacement 1H, en ATR14 1H
MIN_RR_B = 1.2  # RR minimal de la candidate B
ACTIVATION_A_1H = 20  # fenêtre d'activation A1/A2, en bougies 1H
FVG_WINDOW_1H = 24  # fenêtre de recherche du FVG (B), en bougies 1H
ORDER_VALIDITY_B_1H = 20  # validité de l'ordre limite B, en bougies 1H
ACTIVATION_C_1H = 36  # fenêtre de retest (C), en bougies 1H
TIME_EXIT_1H = 128  # sortie temporelle : 32 bougies 4H, en bougies 1H

EMA200_LEN = 200
ATR_LEN = 14

H1 = 3_600_000
H4 = 14_400_000


@dataclass(frozen=True)
class Pools:
    """Pools de liquidité actifs à une clôture 4H (None = inexistant)."""

    ssl: float | None  # sell-side : sous les lows (extrême bas confirmé)
    bsl: float | None  # buy-side : au-dessus des highs


@dataclass(frozen=True)
class RangeInfo:
    """Range valide à une clôture 4H (niveaux figés à cet instant)."""

    ssl: float
    bsl: float
    median: float
    height: float


def atr14_4h(candles: list[Candle]) -> list[float | None]:
    """ATR14 Wilder sur les bougies 4H (série à préfixe)."""
    return atr_series(
        [c.high for c in candles],
        [c.low for c in candles],
        [c.close for c in candles],
        ATR_LEN,
    )


def atr14_1h(candles: list[Candle]) -> list[float | None]:
    """ATR14 Wilder sur les bougies 1H (série à préfixe)."""
    return atr_series(
        [c.high for c in candles],
        [c.low for c in candles],
        [c.close for c in candles],
        ATR_LEN,
    )


def ema200_4h(candles: list[Candle]) -> list[float | None]:
    """EMA200 des clôtures 4H (candidate C uniquement)."""
    return ema_series([c.close for c in candles], EMA200_LEN)


def pool_series(
    candles: list[Candle], window: int = POOL_WINDOW, k: int = SWING_K
) -> list[Pools]:
    """Pools actifs à chaque clôture 4H (LIQUIDITY.md §4.2).

    pool_SSL(t) = plus bas des swings low confirmés (fractale k) dont
    l'extrême est dans la fenêtre [t - window + 1, t - 3] ; l'exigence
    ``confirmed_at <= t`` est équivalente à ``index <= t - 3``. Miroir pour
    pool_BSL. Balayage incrémental : la valeur à t n'utilise que <= t.
    """
    n = len(candles)
    by_confirmation: dict[int, list[SwingPoint]] = defaultdict(list)
    for swing in find_swings(candles, k):
        by_confirmation[swing.confirmed_at].append(swing)

    out: list[Pools] = []
    active: list[SwingPoint] = []
    for t in range(n):
        active.extend(by_confirmation.get(t, ()))
        active = [s for s in active if s.index >= t - window + 1]
        lows = [s.price for s in active if s.kind == "low"]
        highs = [s.price for s in active if s.kind == "high"]
        out.append(
            Pools(
                ssl=min(lows) if lows else None,
                bsl=max(highs) if highs else None,
            )
        )
    return out


def range_series(
    candles: list[Candle],
    pools: list[Pools],
    atrs: list[float | None],
    min_width_atr: float = RANGE_MIN_ATR,
) -> list[RangeInfo | None]:
    """Range valide à chaque clôture 4H (LIQUIDITY.md §4.3).

    Validité : les deux pools existent, largeur >= 5 x ATR14(t), et le prix
    clôture STRICTEMENT à l'intérieur. ``None`` sinon.
    """
    out: list[RangeInfo | None] = []
    for t, candle in enumerate(candles):
        pools_t = pools[t]
        atr_t = atrs[t]
        if pools_t.ssl is None or pools_t.bsl is None or atr_t is None:
            out.append(None)
            continue
        height = pools_t.bsl - pools_t.ssl
        if height < min_width_atr * atr_t:
            out.append(None)
            continue
        if not (pools_t.ssl < candle.close < pools_t.bsl):
            out.append(None)
            continue
        out.append(
            RangeInfo(
                ssl=pools_t.ssl,
                bsl=pools_t.bsl,
                median=(pools_t.ssl + pools_t.bsl) / 2.0,
                height=height,
            )
        )
    return out


def last_swing_series(
    candles: list[Candle], k: int = SWING_K
) -> tuple[list[float | None], list[float | None]]:
    """Dernier swing high / low confirmé à chaque clôture (prix ou None).

    Sert au TP structurel de la candidate B (« au moment du fill »).
    """
    n = len(candles)
    by_confirmation: dict[int, list[SwingPoint]] = defaultdict(list)
    for swing in find_swings(candles, k):
        by_confirmation[swing.confirmed_at].append(swing)

    last_high: list[float | None] = [None] * n
    last_low: list[float | None] = [None] * n
    high = low = None
    for t in range(n):
        for swing in by_confirmation.get(t, ()):
            if swing.kind == "high":
                high = swing.price
            else:
                low = swing.price
        last_high[t] = high
        last_low[t] = low
    return last_high, last_low


def fvg_zone_1h(
    candles_1h: list[Candle],
    atrs_1h: list[float | None],
    u: int,
    pool: float,
    direction: str,
    displacement_atr: float = DISPLACEMENT_ATR_1H,
) -> tuple[float, float] | None:
    """FVG 1H de réversion à la bougie u (LIQUIDITY.md §4.5). None si non
    conforme.

    Sens ``"long"`` (sweep SSL) : bougie haussière, corps >= 1,5 x
    ATR14_1H(u-1), FVG bullish (low[u] > high[u-2]), zone = [high[u-2],
    low[u]] entièrement au-dessus du pool et sous le prix courant. Miroir
    pour ``"short"``.
    """
    if u < 2:
        return None
    atr_u = atrs_1h[u - 1]
    if atr_u is None:
        return None
    cu = candles_1h[u]
    if direction == "long":
        body = cu.close - cu.open
        if body < displacement_atr * atr_u:
            return None
        if cu.low <= candles_1h[u - 2].high:  # pas de FVG bullish
            return None
        zone_low = candles_1h[u - 2].high
        zone_high = cu.low
        if zone_low <= pool or zone_high >= cu.close:  # zone incohérente
            return None
        return zone_low, zone_high
    body = cu.open - cu.close
    if body < displacement_atr * atr_u:
        return None
    if cu.high >= candles_1h[u - 2].low:  # pas de FVG bearish
        return None
    zone_low = cu.high
    zone_high = candles_1h[u - 2].low
    if zone_high >= pool or zone_low <= cu.close:  # zone incohérente
        return None
    return zone_low, zone_high
