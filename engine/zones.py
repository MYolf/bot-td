"""Zones et mouvements : displacement, Fair Value Gaps, Order Blocks.

Famille des DÉCLENCHEURS / zones structurelles (voir CONFLUENCE.md) :
un retest de FVG ou d'Order Block est un événement déclencheur potentiel ;
le displacement est un ingrédient des OB/FVG, pas un signal seul.

Définitions objectives :

- **Displacement** : run d'au plus ``max_bars`` bougies consécutives de même
  direction, dont l'amplitude (open de la première -> close de la dernière)
  atteint ``mult`` x ATR. Émis au premier franchissement du seuil (pas de
  doublon tant que le même run continue).
- **FVG** : gap de 3 bougies (bullish : low[0] > high[2]) de taille >=
  ``min_size_atr`` x ATR, avec suivi de retest / remplissage total /
  expiration. ``with_displacement`` indique si le gap est né d'un
  déplacement (à comparer en étude, pas un filtre dur).
- **Order Block** : pour chaque BOS porté par un displacement, dernière
  bougie opposée avant le déplacement. Zone = [low, high] de cette bougie.
  Suivi : premier retest, mitigation (``mitigation_pct`` de remplissage),
  invalidation (clôture au-delà du bord distal), expiration.

Propriété de préfixe (anti-lookahead) : la création et le suivi des zones ne
dépendent que des bougies déjà clôturées — testé en unitaire.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.indicators import atr as atr_series
from engine.strategy import Candle
from engine.structure import BOS_BEARISH, BOS_BULLISH, market_structure

BULLISH = "bullish"
BEARISH = "bearish"


# ----------------------------------------------------------- displacement --


@dataclass(frozen=True)
class DisplacementEvent:
    """Mouvement impulsif qualifié (au seuil ATR, sans doublon par run)."""

    index: int  # bougie de franchissement du seuil (fin du run au moment t)
    start: int  # première bougie du run directionnel
    direction: str  # bullish / bearish
    move: float  # |close[index] - open[start]|
    atr_multiple: float


def _candle_direction(candle: Candle) -> int:
    if candle.close > candle.open:
        return 1
    if candle.close < candle.open:
        return -1
    return 0


def _directional_run(
    candles: list[Candle], end: int, max_bars: int
) -> tuple[int, int] | None:
    """Run directionnel se terminant en ``end`` : (start, direction), borné."""
    direction = _candle_direction(candles[end])
    if direction == 0:
        return None
    start = end
    while start > 0 and (end - start + 1) < max_bars:
        if _candle_direction(candles[start - 1]) != direction:
            break
        start -= 1
    return start, direction


def _run_move(candles: list[Candle], start: int, end: int) -> float:
    return abs(candles[end].close - candles[start].open)


def displacements(
    candles: list[Candle],
    atr_len: int = 14,
    mult: float = 1.5,
    max_bars: int = 3,
) -> list[DisplacementEvent]:
    """Émet un événement au premier franchissement du seuil par un run.

    Deux bougies consécutives du même run ne produisent qu'un événement
    (le second serait le même mouvement déjà signalé).
    """
    n = len(candles)
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    atrs = atr_series(highs, lows, closes, atr_len)
    events: list[DisplacementEvent] = []

    def _qualifies(end: int) -> tuple[int, int, float] | None:
        a = atrs[end]
        run = _directional_run(candles, end, max_bars)
        if a is None or a <= 0.0 or run is None:
            return None
        start, direction = run
        move = _run_move(candles, start, end)
        if move >= mult * a:
            return start, direction, move
        return None

    for i in range(n):
        current = _qualifies(i)
        if current is None:
            continue
        start, direction, move = current
        previous = _qualifies(i - 1) if i >= 1 else None
        if previous is not None and previous[1] == direction:
            continue  # le même run a déjà émis son événement
        events.append(
            DisplacementEvent(
                index=i,
                start=start,
                direction=BULLISH if direction > 0 else BEARISH,
                move=move,
                atr_multiple=move / atrs[i],  # type: ignore[operator]
            )
        )
    return events


# ------------------------------------------------------------------- FVG ---


@dataclass
class FvgGap:
    """Fair Value Gap suivi dans le temps (champs mis à jour au fil des bougies)."""

    index: int  # bougie de création (la 3e du pattern)
    direction: str  # bullish / bearish
    bottom: float  # bord proximal (high[2] pour un bullish)
    top: float  # bord distal (low[0] pour un bullish)
    size_atr: float | None
    with_displacement: bool
    first_retest_index: int | None = None
    invalidated_at: int | None = None  # remplissage total (wick au travers)
    expired: bool = False


def fair_value_gaps(
    candles: list[Candle],
    atr_len: int = 14,
    min_size_atr: float = 0.25,
    expiry_bars: int = 100,
    disp_mult: float = 1.5,
    disp_max_bars: int = 3,
) -> list[FvgGap]:
    """Détecte et suit les FVG (retest, remplissage total, expiration)."""
    n = len(candles)
    atrs = atr_series(
        [c.high for c in candles], [c.low for c in candles], [c.close for c in candles], atr_len
    )
    disp_indexes = {
        (e.index, e.direction) for e in displacements(candles, atr_len, disp_mult, disp_max_bars)
    }
    gaps: list[FvgGap] = []
    active: list[FvgGap] = []

    for i in range(n):
        # 1. Mise à jour des gaps actifs avec la bougie i.
        still_active: list[FvgGap] = []
        for gap in active:
            if i - gap.index > expiry_bars:
                gap.expired = True
                continue
            candle = candles[i]
            if gap.direction == BULLISH:
                if candle.low <= gap.bottom:  # remplissage total
                    gap.invalidated_at = i
                    continue
                if gap.first_retest_index is None and candle.low <= gap.top:
                    gap.first_retest_index = i
            else:
                if candle.high >= gap.top:  # remplissage total
                    gap.invalidated_at = i
                    continue
                if gap.first_retest_index is None and candle.high >= gap.bottom:
                    gap.first_retest_index = i
            still_active.append(gap)
        active = still_active

        # 2. Détection d'un nouveau gap terminé en i (aucune autoretest).
        if i < 2:
            continue
        a = atrs[i]
        size_threshold = min_size_atr * a if a is not None else None
        with_disp_bull = (i, BULLISH) in disp_indexes or (i - 1, BULLISH) in disp_indexes
        with_disp_bear = (i, BEARISH) in disp_indexes or (i - 1, BEARISH) in disp_indexes
        if candles[i].low > candles[i - 2].high:
            size = candles[i].low - candles[i - 2].high
            if size_threshold is None or size >= size_threshold:
                gap = FvgGap(
                    index=i,
                    direction=BULLISH,
                    bottom=candles[i - 2].high,
                    top=candles[i].low,
                    size_atr=size / a if a else None,
                    with_displacement=with_disp_bull,
                )
                gaps.append(gap)
                active.append(gap)
        elif candles[i].high < candles[i - 2].low:
            size = candles[i - 2].low - candles[i].high
            if size_threshold is None or size >= size_threshold:
                gap = FvgGap(
                    index=i,
                    direction=BEARISH,
                    bottom=candles[i].high,
                    top=candles[i - 2].low,
                    size_atr=size / a if a else None,
                    with_displacement=with_disp_bear,
                )
                gaps.append(gap)
                active.append(gap)

    return gaps


# ----------------------------------------------------------- order block ---


@dataclass
class OrderBlock:
    """Zone d'offre/demande issue d'un BOS porté par un displacement."""

    index: int  # bougie OB (dernière opposée avant le déplacement)
    direction: str  # bullish / bearish
    bottom: float
    top: float
    bos_index: int  # bougie du BOS qui valide la zone
    displacement_start: int  # première bougie du déplacement
    first_retest_index: int | None = None
    first_mitigation_index: int | None = None  # remplissage >= mitigation_pct
    invalidated_at: int | None = None  # clôture au-delà du bord distal
    expired: bool = False


def order_blocks(
    candles: list[Candle],
    swing_k: int = 3,
    atr_len: int = 14,
    disp_mult: float = 1.5,
    disp_max_bars: int = 3,
    ob_lookback: int = 5,
    mitigation_pct: float = 0.5,
    expiry_bars: int = 100,
) -> list[OrderBlock]:
    """Construit les Order Blocks et suit retest / mitigation / invalidation.

    Pour chaque BOS : le run directionnel se terminant sur la bougie de
    cassure doit être un displacement (>= ``disp_mult`` x ATR) ; l'OB est la
    dernière bougie de direction opposée dans les ``ob_lookback`` bougies
    précédant le déplacement. Sans displacement ou sans bougie opposée :
    pas d'OB (évite les zones qui apparaissent partout).
    """
    n = len(candles)
    atrs = atr_series(
        [c.high for c in candles], [c.low for c in candles], [c.close for c in candles], atr_len
    )
    structure = market_structure(candles, swing_k)
    blocks: list[OrderBlock] = []

    for event in structure.events:
        bullish = event.kind == BOS_BULLISH
        run = _directional_run(candles, event.index, disp_max_bars)
        a = atrs[event.index]
        if run is None or a is None or a <= 0.0:
            continue
        start, direction = run
        if (direction > 0) != bullish:
            continue  # le BOS n'est pas porté par un run du même sens
        if _run_move(candles, start, event.index) < disp_mult * a:
            continue  # pas un displacement : pas d'OB

        # Dernière bougie opposée avant le début du déplacement.
        ob_index: int | None = None
        for j in range(start - 1, max(-1, start - 1 - ob_lookback), -1):
            d = _candle_direction(candles[j])
            if (bullish and d < 0) or (not bullish and d > 0):
                ob_index = j
                break
        if ob_index is None:
            continue

        block = OrderBlock(
            index=ob_index,
            direction=BULLISH if bullish else BEARISH,
            bottom=candles[ob_index].low,
            top=candles[ob_index].high,
            bos_index=event.index,
            displacement_start=start,
        )
        blocks.append(block)

        # Suivi après le BOS (une seule passe : les bougies suivantes).
        for i in range(event.index + 1, n):
            if i - block.index > expiry_bars:
                block.expired = True
                break
            candle = candles[i]
            if bullish:
                if candle.close < block.bottom:
                    block.invalidated_at = i
                    break
                if candle.low <= block.top:
                    if block.first_retest_index is None:
                        block.first_retest_index = i
                    if (
                        block.first_mitigation_index is None
                        and candle.low <= block.top - mitigation_pct * (block.top - block.bottom)
                    ):
                        block.first_mitigation_index = i
            else:
                if candle.close > block.top:
                    block.invalidated_at = i
                    break
                if candle.high >= block.bottom:
                    if block.first_retest_index is None:
                        block.first_retest_index = i
                    if (
                        block.first_mitigation_index is None
                        and candle.high >= block.bottom + mitigation_pct * (block.top - block.bottom)
                    ):
                        block.first_mitigation_index = i

    return blocks
