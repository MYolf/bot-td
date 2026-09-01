"""Tests de la structure de marché (swings, BOS/CHOCH).

Deux garanties fondamentales :
1. définitions objectives sur des séries construites à la main ;
2. propriété de PRÉFIXE (anti-lookahead) : calculer sur ``candles[:t+1]``
   donne exactement le préfixe du calcul sur ``candles``.
"""

from __future__ import annotations

import random

from engine.structure import find_swings, liquidity_sweeps, market_structure
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


# --------------------------------------------------------- liquidity sweep


def _sw(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        open_time=i * TF_MS,
        close_time=i * TF_MS + TF_MS - 1,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=10.0,
    )


def _sweep_series(pierce: Candle | None = None, n: int = 11) -> list[Candle]:
    """Série plate avec un swing low en 2 (low 97), confirmé en 4 (k=2)."""
    candles = []
    for i in range(n):
        if i == 2:
            candles.append(_sw(i, 100.0, 100.5, 97.0, 100.0))
        elif i == 10 and pierce is not None:
            candles.append(pierce)
        else:
            candles.append(_sw(i, 100.0, 100.5, 99.0, 100.0))
    return candles


def test_sweep_balisque_basse_confirme() -> None:
    # Bougie 10 : mèche sous 97 (profondeur 0.5 > 0.1 x ATR ~ 1.5), clôture
    # de récupération au-dessus -> sweep bullish confirmé sur la même bougie.
    candles = _sweep_series(_sw(10, 100.0, 101.0, 96.5, 100.0))
    sweeps = liquidity_sweeps(candles, k=2, atr_len=2, depth_atr=0.1, min_age=5, confirm_bars=3)
    assert len(sweeps) == 1
    s = sweeps[0]
    assert (s.index, s.direction, s.swept_level) == (10, "bullish", 97.0)
    assert s.swing_index == 2
    assert s.pierce_index == 10


def test_sweep_sans_recuperation_fenetre_expiree() -> None:
    # Percée avec clôture légèrement sous le niveau (96.9), jamais de
    # récupération : la fenêtre de 3 bougies expire -> aucun événement.
    candles = _sweep_series(_sw(10, 100.0, 101.0, 96.5, 96.9), n=14)
    for i in range(11, 14):
        candles[i] = _sw(i, 96.9, 97.4, 96.4, 96.9)
    sweeps = liquidity_sweeps(candles, k=2, atr_len=2, depth_atr=0.1, min_age=5, confirm_bars=3)
    assert sweeps == []


def test_sweep_cassure_profonde_pas_un_sweep() -> None:
    # Clôture franchement sous le niveau : vraie cassure, pas un balayage.
    candles = _sweep_series(_sw(10, 100.0, 101.0, 95.0, 96.0), n=13)
    for i in range(11, 13):
        candles[i] = _sw(i, 96.0, 96.5, 95.5, 96.0)
    sweeps = liquidity_sweeps(candles, k=2, atr_len=2, depth_atr=0.1, min_age=5, confirm_bars=3)
    assert sweeps == []


def test_sweep_trop_jeune_pas_pris() -> None:
    # Percée dès la bougie 6 : âge du swing = 4 < min_age 5 -> ignoré.
    candles = _sweep_series(_sw(10, 100.0, 100.5, 99.0, 100.0), n=7)
    candles[6] = _sw(6, 100.0, 101.0, 96.5, 100.0)
    sweeps = liquidity_sweeps(candles, k=2, atr_len=2, depth_atr=0.1, min_age=5, confirm_bars=3)
    assert sweeps == []


def test_propriete_de_prefixe_sweeps() -> None:
    rng = random.Random(99)
    candles = [
        Candle(
            open_time=i * TF_MS,
            close_time=i * TF_MS + TF_MS - 1,
            open=100.0,
            high=100.0 + rng.uniform(0.5, 3.0),
            low=100.0 - rng.uniform(0.5, 3.0),
            close=100.0 + rng.uniform(-2.5, 2.5),
            volume=rng.uniform(5.0, 50.0),
        )
        for i in range(160)
    ]
    full = liquidity_sweeps(candles, k=3, atr_len=3, min_age=8)
    assert len(full) > 0  # la série aléatoire produit bien des balayages
    for cut in (60, 120, 159):
        assert liquidity_sweeps(candles[: cut + 1], k=3, atr_len=3, min_age=8) == [
            e for e in full if e.index <= cut
        ]
