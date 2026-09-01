"""Tests de la structure de marché (swings, BOS/CHOCH).

Deux garanties fondamentales :
1. définitions objectives sur des séries construites à la main ;
2. propriété de PRÉFIXE (anti-lookahead) : calculer sur ``candles[:t+1]``
   donne exactement le préfixe du calcul sur ``candles``.
"""

from __future__ import annotations

import random

from engine.structure import find_swings, market_structure
from engine.strategy import Candle

TF_MS = 900_000  # 15 minutes


def _mk(closes: list[float]) -> list[Candle]:
    return [
        Candle(
            open_time=i * TF_MS,
            close_time=i * TF_MS + TF_MS - 1,
            open=close,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=10.0,
        )
        for i, close in enumerate(closes)
    ]


# Série construite à la main (k=2) :
#   - swing high en 3 (13), confirmé en 5
#   - swing low  en 5 (10), confirmé en 7
#   - swing high en 9 (16), confirmé en 11
#   - swing low  en 14 (11.9), confirmé en 16
#   - BOS bullish en 7 (clôture 14 > 13), BOS bearish en 17 (11.5 < 11.9 = CHOCH)
SERIES = [
    10.0, 10.5, 11.0, 12.0, 11.5, 11.0, 12.5, 14.0, 14.5, 15.0,
    14.8, 14.3, 14.0, 13.5, 12.9, 13.4, 13.8, 11.5,
]


def test_swings_detectes_avec_confirmation_retardee() -> None:
    swings = find_swings(_mk(SERIES), k=2)
    assert [(s.index, s.kind, s.price, s.confirmed_at) for s in swings] == [
        (3, "high", 13.0, 5),
        (5, "low", 10.0, 7),
        (9, "high", 16.0, 11),
        (14, "low", 11.9, 16),
    ]


def test_egalites_de_niveaux_ne_comptent_pas() -> None:
    # Série plate : aucun strict extrême local => aucun swing.
    assert find_swings(_mk([10.0] * 6), k=2) == []


def test_bos_et_choch_sur_cloture() -> None:
    result = market_structure(_mk(SERIES), k=2)
    assert [(e.index, e.kind, e.is_choch, e.broken_level) for e in result.events] == [
        (7, "bos_bullish", False, 13.0),
        (17, "bos_bearish", True, 11.9),
    ]


def test_bias_par_bougie() -> None:
    result = market_structure(_mk(SERIES), k=2)
    assert result.bias[:7] == [""] * 7
    assert set(result.bias[7:17]) == {"bullish"}
    assert result.bias[17] == "bearish"


def test_wick_seul_ne_casse_pas_la_structure() -> None:
    # La bougie 8 touche 16.1 en high mais clôture sous le swing high 13*1.0 :
    # on force un high qui dépasse le swing 13 sans clôture au-dessus.
    candles = _mk(SERIES)
    # Reconstruit la bougie 6 avec un wick au-dessus de 13 mais clôture 12.5.
    candles[6] = Candle(
        open_time=6 * TF_MS,
        close_time=6 * TF_MS + TF_MS - 1,
        open=12.5,
        high=13.5,  # wick au-dessus du swing high 13
        low=11.5,
        close=12.5,  # clôture SOUS 13 : pas de BOS
        volume=10.0,
    )
    result = market_structure(candles, k=2)
    # Le BOS bullish n'arrive qu'en 7, jamais en 6 (wick ignoré).
    assert [e.index for e in result.events if e.kind == "bos_bullish"] == [7]


def test_propriete_de_prefixe_swings_et_structure() -> None:
    rng = random.Random(42)
    candles = [
        Candle(
            open_time=i * TF_MS,
            close_time=i * TF_MS + TF_MS - 1,
            open=100.0,
            high=100.0 + rng.uniform(0.5, 3.0),
            low=100.0 - rng.uniform(0.5, 3.0),
            close=100.0 + rng.uniform(-2.0, 2.0),
            volume=rng.uniform(5.0, 50.0),
        )
        for i in range(140)
    ]
    full_swings = find_swings(candles)
    full_structure = market_structure(candles)
    for cut in (40, 80, 120, 139):
        prefix = candles[: cut + 1]
        # Swings : seuls ceux confirmés à <= cut existent, à l'identique.
        assert find_swings(prefix) == [
            s for s in full_swings if s.confirmed_at <= cut
        ]
        partial = market_structure(prefix)
        assert partial.events == [
            e for e in full_structure.events if e.index <= cut
        ]
        assert partial.bias == full_structure.bias[: cut + 1]
