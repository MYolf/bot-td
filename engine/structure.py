"""Structure de marché objective : swings fractals confirmés, BOS / CHOCH.

Famille des DÉCLENCHEURS : ce sont les seuls éléments autorisés à créer un
signal (voir CONFLUENCE.md — les indicateurs qualifient, ils ne déclenchent
jamais).

Définitions (totalement objectives, aucune interprétation humaine) :

- **Swing** : fractale à ``k`` bougies de chaque côté. Swing high en ``i`` si
  ``high[i]`` est strictement supérieur aux ``2k`` high voisins (idem low).
  Les égalités ne comptent pas (strict, comme les fractales Pine).
- **Confirmation** : un swing en ``i`` n'est utilisable qu'à partir de la
  bougie ``i + k`` — jamais avant (anti-lookahead).
- **BOS (Break Of Structure)** : clôture au-delà du dernier swing confirmé
  non encore cassé (clôture, pas un wick — moins de faux signaux).
- **CHOCH (Change Of Character)** : BOS dans la direction opposée au
  précédent BOS.

Contrat anti-lookahead (propriété de préfixe, testée en test unitaire) :
``find_swings(candles[:t+1]) == [s for s in find_swings(candles) if s.confirmed_at <= t]``
et de même pour ``market_structure``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from engine.indicators import atr as atr_series
from engine.strategy import Candle

DEFAULT_SWING_K = 3

BOS_BULLISH = "bos_bullish"
BOS_BEARISH = "bos_bearish"


@dataclass(frozen=True)
class SwingPoint:
    """Extrême local confirmé (fractale à k bougies de chaque côté)."""

    index: int  # bougie de l'extrême
    price: float  # high (swing high) ou low (swing low)
    kind: str  # "high" ou "low"
    confirmed_at: int  # bougie à partir de laquelle le swing est utilisable


@dataclass(frozen=True)
class StructureEvent:
    """Cassure de structure (BOS), éventuellement un CHOCH."""

    index: int  # bougie qui clôture la cassure
    kind: str  # BOS_BULLISH ou BOS_BEARISH
    is_choch: bool  # direction opposée au BOS précédent
    broken_level: float  # niveau cassé (prix du swing)
    swing_index: int  # bougie du swing cassé


@dataclass(frozen=True)
class StructureResult:
    events: list[StructureEvent]
    bias: list[str]  # par bougie : direction du dernier BOS ("" si aucun)


def find_swings(candles: list[Candle], k: int = DEFAULT_SWING_K) -> list[SwingPoint]:
    """Détecte tous les swings confirmés, triés par bougie d'extrême.

    Les swings dont la confirmation tomberait après la dernière bougie ne
    sont PAS retournés (ils n'existent pas encore — anti-lookahead).
    """
    swings: list[SwingPoint] = []
    n = len(candles)
    for i in range(k, n - k):
        window = range(i - k, i + k + 1)
        if all(candles[i].high > candles[j].high for j in window if j != i):
            swings.append(SwingPoint(i, candles[i].high, "high", i + k))
            continue
        if all(candles[i].low < candles[j].low for j in window if j != i):
            swings.append(SwingPoint(i, candles[i].low, "low", i + k))
    return swings


def market_structure(candles: list[Candle], k: int = DEFAULT_SWING_K) -> StructureResult:
    """Rejoue la structure bougie par bougie : BOS sur clôture + CHOCH.

    La référence est le **dernier swing confirmé non cassé** de chaque côté.
    Un swing tout juste confirmé ne peut pas être cassé à sa bougie de
    confirmation (il est, par définition, l'extrême strict de sa fenêtre).
    """
    n = len(candles)
    events: list[StructureEvent] = []
    bias: list[str] = [""] * n

    by_confirmation: dict[int, list[SwingPoint]] = defaultdict(list)
    for swing in find_swings(candles, k):
        by_confirmation[swing.confirmed_at].append(swing)

    ref_high: SwingPoint | None = None
    ref_low: SwingPoint | None = None
    last_direction = ""

    for i in range(n):
        for swing in by_confirmation.get(i, ()):
            if swing.kind == "high":
                ref_high = swing
            else:
                ref_low = swing

        close = candles[i].close
        if ref_high is not None and close > ref_high.price:
            events.append(
                StructureEvent(
                    index=i,
                    kind=BOS_BULLISH,
                    is_choch=last_direction == "bearish",
                    broken_level=ref_high.price,
                    swing_index=ref_high.index,
                )
            )
            last_direction = "bullish"
            ref_high = None
        if ref_low is not None and close < ref_low.price:
            events.append(
                StructureEvent(
                    index=i,
                    kind=BOS_BEARISH,
                    is_choch=last_direction == "bullish",
                    broken_level=ref_low.price,
                    swing_index=ref_low.index,
                )
            )
            last_direction = "bearish"
            ref_low = None

        bias[i] = last_direction

    return StructureResult(events=events, bias=bias)


# ----------------------------------------------------- liquidity sweeps ----


@dataclass(frozen=True)
class SweepEvent:
    """Balayage de liquidité confirmé (événement DÉCLENCHEUR)."""

    index: int  # bougie de confirmation (clôture de récupération)
    direction: str  # "bullish" (lows balayés) / "bearish" (highs balayés)
    swept_level: float  # niveau du swing balayé
    swing_index: int  # bougie du swing balayé
    pierce_index: int  # bougie qui a percé le niveau


def liquidity_sweeps(
    candles: list[Candle],
    k: int = 5,
    atr_len: int = 14,
    depth_atr: float = 0.1,
    min_age: int = 10,
    confirm_bars: int = 3,
) -> list[SweepEvent]:
    """Détecte les liquidity sweeps, définition totalement objective.

    Un swing low ``S`` (fractale ``k``, confirmé) est « balayé » lorsque :

    1. une bougie perce ``S`` par le bas d'au moins ``depth_atr`` x ATR
       (la liquidité sous le niveau est prise) ;
    2. une clôture revient AU-DESSUS de ``S`` dans les ``confirm_bars``
       bougies qui suivent la percée (index de l'événement = cette clôture) ;
    3. le swing est mort si une clôture passe franchement sous le niveau
       (moins ``depth_atr`` x ATR : vraie cassure, pas un balayage), ou si
       l'âge du swing à la percée est < ``min_age`` (liquidité trop fraîche).

    Miroir exact pour les swing highs (direction bearish). Chaque swing ne
    peut être balayé qu'une fois. Propriété de préfixe : testée en unitaire.
    """
    n = len(candles)
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    atrs = atr_series(highs, lows, closes, atr_len)

    by_confirmation: dict[int, list[SwingPoint]] = defaultdict(list)
    for swing in find_swings(candles, k):
        by_confirmation[swing.confirmed_at].append(swing)

    events: list[SweepEvent] = []
    available: list[SwingPoint] = []  # swings confirmés, non cassés, non balayés
    # pendings : index du swing -> bougie de percée
    pendings: dict[int, int] = {}

    for i in range(n):
        available.extend(by_confirmation.get(i, ()))
        a = atrs[i]

        survivors: list[SwingPoint] = []
        for swing in available:
            level = swing.price
            if swing.index in pendings:
                # Fenêtre de récupération ouverte après la percée.
                if closes[i] > level:
                    events.append(
                        SweepEvent(
                            index=i,
                            direction="bullish" if swing.kind == "low" else "bearish",
                            swept_level=level,
                            swing_index=swing.index,
                            pierce_index=pendings.pop(swing.index),
                        )
                    )
                    continue  # swing consommé
                if a is not None and (
                    (swing.kind == "low" and closes[i] < level - depth_atr * a)
                    or (swing.kind == "high" and closes[i] > level + depth_atr * a)
                ):
                    pendings.pop(swing.index)  # vraie cassure en profondeur
                    continue
                if i - pendings[swing.index] >= confirm_bars:
                    pendings.pop(swing.index)  # fenêtre expirée sans récupération
                    continue
                survivors.append(swing)
                continue

            pierced = a is not None and (
                (swing.kind == "low" and lows[i] <= level - depth_atr * a)
                or (swing.kind == "high" and highs[i] >= level + depth_atr * a)
            )
            broken = (
                (swing.kind == "low" and closes[i] < level)
                or (swing.kind == "high" and closes[i] > level)
            )
            if pierced and i - swing.index >= min_age:
                if not broken:
                    events.append(
                        SweepEvent(
                            index=i,
                            direction="bullish" if swing.kind == "low" else "bearish",
                            swept_level=level,
                            swing_index=swing.index,
                            pierce_index=i,
                        )
                    )
                    continue  # balayage confirmé sur la même bougie
                pendings[swing.index] = i  # clôturé sous le niveau : fenêtre ouverte
                survivors.append(swing)
                continue
            if broken or pierced:
                continue  # cassé, ou percé trop tôt (âge insuffisant) : mort
            survivors.append(swing)

        available = survivors

    return events
