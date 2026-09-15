"""Tests des primitives LIQUIDITY.md §4 (engine/liquidity/primitives.py).

Fixtures synthétiques 4H : plateau à clôtures 100 avec swings isolés (fractale
k=3 stricte — les plateaux à valeurs égales ne créent AUCUN swing car les
comparaisons sont strictes).
"""

from __future__ import annotations

import random

import pytest

from engine.liquidity.primitives import (
    H4,
    atr14_4h,
    fvg_zone_1h,
    last_swing_series,
    pool_series,
    range_series,
)
from engine.strategy import Candle


def mk4(i: int, o: float, h: float, l: float, cl: float) -> Candle:
    return Candle(i * H4, i * H4 + H4 - 1, o, h, l, cl, 1000.0)


def plateau(n: int = 60, high: float = 101.0, low: float = 99.0) -> list[Candle]:
    return [mk4(i, 100.0, high, low, 100.0) for i in range(n)]


# ------------------------------------------------------------- pool_series


class TestPoolSeries:
    def test_aucun_pool_avant_premiere_confirmation(self):
        # Swing low à l'index 10 (confirmé à 13) : rien avant t = 13.
        candles = plateau()
        candles[10] = mk4(10, 100.0, 101.0, 90.0, 100.0)
        pools = pool_series(candles)
        assert pools[12].ssl is None
        assert pools[13].ssl == 90.0

    def test_pools_deux_cotes(self):
        candles = plateau()
        candles[10] = mk4(10, 100.0, 101.0, 90.0, 100.0)  # low, confirmé à 13
        candles[30] = mk4(30, 100.0, 110.0, 99.0, 100.0)  # high, confirmé à 33
        pools = pool_series(candles)
        assert pools[32] == pool_series(candles)[32]
        assert pools[32].ssl == 90.0 and pools[32].bsl is None
        assert pools[33].ssl == 90.0 and pools[33].bsl == 110.0

    def test_fenetre_glissante_expire_les_vieux_swings(self):
        # window=20 : le swing d'index 10 est actif tant que 10 >= t - 19,
        # donc jusqu'à t = 29 ; plus rien ensuite.
        candles = plateau()
        candles[10] = mk4(10, 100.0, 101.0, 90.0, 100.0)
        pools = pool_series(candles, window=20)
        assert pools[29].ssl == 90.0
        assert pools[30].ssl is None

    def test_le_plus_bas_des_swings_actifs_gagne(self):
        candles = plateau()
        candles[10] = mk4(10, 100.0, 101.0, 95.0, 100.0)
        candles[25] = mk4(25, 100.0, 101.0, 90.0, 100.0)  # plus bas, confirmé à 28
        pools = pool_series(candles)
        assert pools[27].ssl == 95.0
        assert pools[28].ssl == 90.0


# ------------------------------------------------------------ range_series


class TestRangeSeries:
    def test_range_valide(self):
        # Plateau TR = 2 (ATR14 = 2 dès t=13) ; largeur 110-90 = 20 >= 5x2.
        candles = plateau()
        candles[10] = mk4(10, 100.0, 101.0, 90.0, 100.0)
        candles[30] = mk4(30, 100.0, 110.0, 99.0, 100.0)
        ranges = range_series(candles, pool_series(candles), atr14_4h(candles))
        assert ranges[32] is None  # BSL pas encore confirmé
        rng = ranges[33]
        assert rng is not None
        assert rng.ssl == 90.0 and rng.bsl == 110.0
        assert rng.median == 100.0 and rng.height == 20.0

    def test_range_rejete_trop_etroit(self):
        # Plateau TR = 5 (ATR = 5) ; largeur 20 < 5x5 = 25 -> jamais valide.
        candles = plateau(high=102.5, low=97.5)
        candles[10] = mk4(10, 100.0, 102.5, 90.0, 100.0)
        candles[30] = mk4(30, 100.0, 110.0, 97.5, 100.0)
        ranges = range_series(candles, pool_series(candles), atr14_4h(candles))
        assert all(r is None for r in ranges)

    def test_range_rejete_cloture_hors_bornes(self):
        candles = plateau()
        candles[10] = mk4(10, 100.0, 101.0, 90.0, 100.0)
        candles[30] = mk4(30, 100.0, 110.0, 99.0, 100.0)
        candles[45] = mk4(45, 100.0, 111.0, 99.0, 111.0)  # clôture au-dessus du BSL
        ranges = range_series(candles, pool_series(candles), atr14_4h(candles))
        assert ranges[44] is not None
        assert ranges[45] is None

    def test_range_rejete_cloture_au_niveau_exact(self):
        # Clôture ÉGALE au BSL : « strictement à l'intérieur » exige <.
        candles = plateau()
        candles[10] = mk4(10, 100.0, 101.0, 90.0, 100.0)
        candles[30] = mk4(30, 100.0, 110.0, 99.0, 100.0)
        candles[45] = mk4(45, 100.0, 111.0, 99.0, 110.0)
        ranges = range_series(candles, pool_series(candles), atr14_4h(candles))
        assert ranges[45] is None


# -------------------------------------------------------- last_swing_series


class TestLastSwingSeries:
    def test_dernier_swing_confirme_par_cote(self):
        candles = plateau()
        candles[10] = mk4(10, 100.0, 101.0, 90.0, 100.0)  # low, confirmé à 13
        candles[30] = mk4(30, 100.0, 110.0, 99.0, 100.0)  # high, confirmé à 33
        highs, lows = last_swing_series(candles)
        assert highs[32] is None and lows[32] == 90.0
        assert highs[33] == 110.0 and lows[33] == 90.0


# ------------------------------------------------------------- fvg_zone_1h


def _c1_list(specs: list[tuple[float, float, float, float]]):
    return [
        Candle(j * 3_600_000, j * 3_600_000 + 3_599_999, o, h, l, cl, 10.0)
        for j, (o, h, l, cl) in enumerate(specs)
    ]


class TestFvgZone1h:
    def test_fvg_bullish_valide(self):
        candles = _c1_list(
            [
                (99.0, 99.4, 98.8, 99.2),  # u-3
                (99.8, 99.9, 99.7, 99.85),  # u-2 : high 99.9
                (99.85, 100.0, 99.8, 99.9),  # u-1
                (99.9, 101.2, 100.0, 101.0),  # u : corps 1.1, gap
            ]
        )
        atrs = [None] * 4
        atrs[2] = 0.5  # ATR14_1H(u-1) : 1.5 x 0.5 = 0.75 <= corps 1.1
        zone = fvg_zone_1h(candles, atrs, 3, pool=98.0, direction="long")
        assert zone == (99.9, 100.0)

    def test_corps_trop_petit(self):
        candles = _c1_list(
            [
                (99.0, 99.4, 98.8, 99.2),
                (99.8, 99.9, 99.7, 99.85),
                (99.85, 100.0, 99.8, 99.9),
                (99.9, 100.4, 100.0, 100.3),  # corps 0.4 < 0.75
            ]
        )
        atrs = [None] * 4
        atrs[2] = 0.5
        assert fvg_zone_1h(candles, atrs, 3, 98.0, "long") is None

    def test_pas_de_gap(self):
        candles = _c1_list(
            [
                (99.0, 100.5, 98.8, 99.2),
                (99.8, 100.6, 99.7, 99.85),  # high(u-2) = 100.6
                (99.85, 100.7, 99.8, 99.9),
                (99.9, 101.4, 100.5, 101.0),  # low(u) = 100.5 <= 100.6 : pas de gap
            ]
        )
        atrs = [None] * 4
        atrs[2] = 0.5
        assert fvg_zone_1h(candles, atrs, 3, 98.0, "long") is None

    def test_zone_sous_le_pool(self):
        # Zone entièrement sous le pool SSL : incohérente pour un long.
        candles = _c1_list(
            [
                (96.0, 96.4, 95.8, 96.2),
                (96.8, 96.9, 96.7, 96.85),
                (96.85, 97.0, 96.8, 96.9),
                (96.9, 98.2, 97.0, 97.9),
            ]
        )
        atrs = [None] * 4
        atrs[2] = 0.5
        assert fvg_zone_1h(candles, atrs, 3, pool=97.5, direction="long") is None

    def test_fvg_bearish_valide(self):
        candles = _c1_list(
            [
                (101.0, 101.2, 100.6, 100.8),
                (100.2, 100.3, 100.1, 100.15),  # u-2 : low 100.1
                (100.15, 100.3, 100.1, 100.2),
                (100.0, 100.05, 98.9, 99.0),  # u : corps baissier 1.0, high < low(u-2)
            ]
        )
        atrs = [None] * 4
        atrs[2] = 0.5
        zone = fvg_zone_1h(candles, atrs, 3, pool=102.0, direction="short")
        assert zone == (100.05, 100.1)  # [high(u), low(u-2)]

    def test_atr_indisponible(self):
        candles = _c1_list(
            [
                (99.0, 99.4, 98.8, 99.2),
                (99.8, 99.9, 99.7, 99.85),
                (99.85, 100.0, 99.8, 99.9),
                (99.9, 101.2, 100.0, 101.0),
            ]
        )
        assert fvg_zone_1h(candles, [None] * 4, 3, 98.0, "long") is None


# ------------------------------------------------- propriété de préfixe ----


def _marche_aleatoire(n: int = 60, seed: int = 2026) -> list[Candle]:
    rng = random.Random(seed)
    candles: list[Candle] = []
    p = 100.0
    for i in range(n):
        o = p
        cl = o + rng.uniform(-1.5, 1.5)
        h = max(o, cl) + rng.uniform(0.0, 0.8)
        l = min(o, cl) - rng.uniform(0.0, 0.8)
        candles.append(mk4(i, o, h, l, cl))
        p = cl
    return candles


class TestProprietePrefixe:
    """Anti-lookahead : la valeur à t n'utilise que des bougies <= t."""

    POINTS = (10, 17, 25, 40, 52)

    def test_pool_series(self):
        candles = _marche_aleatoire()
        full = pool_series(candles)
        for t in self.POINTS:
            assert pool_series(candles[: t + 1]) == full[: t + 1]

    def test_range_series(self):
        candles = _marche_aleatoire()
        full = range_series(candles, pool_series(candles), atr14_4h(candles))
        for t in self.POINTS:
            partial = range_series(
                candles[: t + 1], pool_series(candles[: t + 1]), atr14_4h(candles[: t + 1])
            )
            assert partial == full[: t + 1]

    def test_last_swing_series(self):
        candles = _marche_aleatoire()
        full_h, full_l = last_swing_series(candles)
        for t in self.POINTS:
            part_h, part_l = last_swing_series(candles[: t + 1])
            assert part_h == full_h[: t + 1]
            assert part_l == full_l[: t + 1]
