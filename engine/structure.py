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
