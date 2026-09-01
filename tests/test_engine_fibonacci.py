"""Tests du module Fibonacci (Phase 32, FIBONACCI.md scellé).

Mêmes garanties que la famille structure : définitions objectives vérifiées
sur des séries construites à la main, et propriété de PRÉFIXE
(anti-lookahead). Le scénario haussier de référence (BASE) fournit une
impulsion qualifiée : swing low 95.5 (bougie 14) -> swing high 131.0
(bougie 26), confirmée en 29, avec BOS haussier en 20 et displacement dans
le leg. Bande 0.50-0.70 = [106.15, 113.25].
"""

from __future__ import annotations

import random

import pytest

from engine.fibonacci import (
    ACTIVE,
    CONSUMED,
    FibImpulse,
    FibParams,
    FibState,
    INVALIDATED,
    EXPIRED,
    fib_states,
    ob_fib_grade,
)
from engine.strategy import Candle

TF_MS = 900_000

PARAMS = FibParams(atr_len=2, min_amplitude_atr=1.0, disp_mult=1.0)

MIRROR = 231.0  # transformation p -> 231 - p (miroir vertical du scénario)

# (open, high, low, close). Swing high en 6 (103.5), swing low en 14 (95.5),
# rally 18-26, BOS haussier en 20 (clôture 103.8 > 103.5), swing high en 26
# (131.0) confirmé en 29.
BASE: list[tuple[float, float, float, float]] = [
    (100.0, 100.5, 99.5, 100.0),  # 0
    (100.0, 100.5, 99.5, 100.0),  # 1
    (100.0, 100.5, 99.5, 100.0),  # 2
    (100.0, 100.5, 99.5, 100.0),  # 3
    (100.0, 100.5, 99.5, 100.0),  # 4
    (100.0, 100.5, 99.5, 100.0),  # 5
    (100.5, 103.5, 100.4, 103.0),  # 6  swing high 103.5 (confirmé en 9)
    (103.0, 103.0, 101.0, 101.5),  # 7
    (101.5, 102.0, 100.5, 101.0),  # 8
    (101.0, 101.5, 100.5, 100.8),  # 9
    (100.8, 101.2, 100.2, 100.5),  # 10
    (100.5, 101.0, 99.8, 100.2),  # 11
    (100.2, 100.8, 97.5, 98.0),  # 12
    (98.0, 98.5, 96.8, 97.2),  # 13
    (97.2, 97.6, 95.5, 96.0),  # 14 swing low 95.5 (confirmé en 17)
    (96.0, 97.5, 96.2, 97.0),  # 15
    (97.0, 97.6, 96.0, 96.8),  # 16
    (96.8, 97.8, 96.3, 97.2),  # 17
    (97.2, 99.0, 97.0, 98.8),  # 18
    (98.8, 101.0, 98.5, 100.8),  # 19
    (100.8, 104.0, 100.5, 103.8),  # 20 BOS haussier + displacement
    (103.8, 107.0, 103.5, 106.8),  # 21
    (106.8, 110.0, 106.5, 109.8),  # 22
    (109.8, 114.0, 109.5, 113.8),  # 23
    (113.8, 118.0, 113.5, 117.8),  # 24
    (117.8, 122.0, 117.5, 121.8),  # 25
    (121.8, 131.0, 121.5, 130.5),  # 26 swing high 131.0 (confirmé en 29)
    (130.5, 130.8, 127.0, 127.5),  # 27
    (127.5, 128.5, 125.0, 125.5),  # 28
    (125.5, 127.0, 124.5, 126.0),  # 29 création du Fib
]

# Retracement : épisodes de contact avec la bande [106.15, 113.25].
CONSUMPTION_TAIL: list[tuple[float, float, float, float]] = [
    (126.0, 126.5, 120.0, 121.0),  # 30 hors bande
    (121.0, 121.5, 112.0, 113.0),  # 31 épisode 1
    (113.0, 116.0, 111.0, 115.5),  # 32 encore dans la bande (même épisode)
    (115.5, 121.0, 115.0, 120.5),  # 33 sortie de bande
    (120.5, 121.0, 112.5, 113.5),  # 34 épisode 2
    (113.5, 114.0, 110.0, 111.5),  # 35 encore dans la bande
    (111.5, 118.0, 114.5, 117.5),  # 36 sortie de bande
    (117.5, 118.0, 112.0, 113.0),  # 37 épisode 3 -> consommé
    (113.0, 114.0, 111.0, 112.0),  # 38 plus aucun Fib
]

# Chute sous le niveau 1.0 (95.5) : invalidation.
INVALIDATION_TAIL: list[tuple[float, float, float, float]] = [
    (126.0, 126.5, 120.0, 121.0),  # 30
    (121.0, 121.5, 112.0, 113.0),  # 31 épisode 1
    (113.0, 113.5, 104.0, 104.5),  # 32
    (104.5, 105.0, 94.0, 94.5),  # 33 clôture < 95.5 -> invalidé
    (94.5, 96.0, 93.0, 95.0),  # 34 plus aucun Fib
]

# Nouvelle impulsion (swing low 97.0 en 36, swing high 140.0 en 43, BOS en 42)
# qui remplace la première, encore active.
REPLACEMENT_TAIL: list[tuple[float, float, float, float]] = [
    (126.0, 126.5, 120.0, 121.0),  # 30
    (121.0, 121.5, 112.0, 113.0),  # 31 épisode 1 (bande de l'ancien Fib)
    (113.0, 114.0, 107.0, 108.0),  # 32
    (108.0, 109.0, 102.5, 103.0),  # 33
    (103.0, 104.0, 100.0, 100.5),  # 34
    (100.5, 101.5, 98.0, 98.5),  # 35
    (98.5, 99.0, 97.0, 97.8),  # 36 swing low 97.0 (confirmé en 39)
    (97.8, 102.0, 97.5, 101.5),  # 37
    (101.5, 107.0, 101.0, 106.5),  # 38
    (106.5, 112.0, 106.0, 111.5),  # 39
    (111.5, 117.0, 111.0, 116.5),  # 40
    (116.5, 124.0, 116.0, 123.5),  # 41
    (123.5, 132.5, 123.0, 131.8),  # 42 BOS haussier (clôture > 131.0)
    (131.8, 140.0, 131.0, 139.5),  # 43 swing high 140.0 (confirmé en 46)
    (139.5, 139.8, 135.0, 135.5),  # 44
    (135.5, 136.5, 132.0, 132.5),  # 45 ancien Fib encore actif
    (132.5, 134.0, 131.5, 133.0),  # 46 remplacement par le nouveau Fib
]


def _candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        open_time=i * TF_MS,
        close_time=i * TF_MS + TF_MS - 1,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=10.0,
    )


def _mk(rows: list[tuple[float, float, float, float]]) -> list[Candle]:
    return [_candle(i, o, h, l, c) for i, (o, h, l, c) in enumerate(rows)]


def _mirror(candles: list[Candle]) -> list[Candle]:
    """Miroir vertical p -> MIRROR - p : le scénario haussier devient baissier."""

    def flip(price: float) -> float:
        return round(MIRROR - price, 10)

    return [
        Candle(
            open_time=c.open_time,
            close_time=c.close_time,
            open=flip(c.open),
            high=flip(c.low),
            low=flip(c.high),
            close=flip(c.close),
            volume=c.volume,
        )
        for c in candles
    ]


# ------------------------------------------------------------ convention ---


def test_convention_niveaux_haussier() -> None:
    imp = FibImpulse("bullish", 0, 10, 10, swing_low=100.0, swing_high=200.0)
    assert imp.level(0.0) == pytest.approx(200.0)  # 0 au sommet
    assert imp.level(0.5) == pytest.approx(150.0)
    assert imp.level(0.618) == pytest.approx(138.2)
    assert imp.level(0.786) == pytest.approx(121.4)
    assert imp.level(1.0) == pytest.approx(100.0)  # 1 à l'origine


def test_convention_niveaux_baissier() -> None:
    imp = FibImpulse("bearish", 0, 10, 10, swing_low=100.0, swing_high=200.0)
    assert imp.level(0.0) == pytest.approx(100.0)  # 0 au creux
    assert imp.level(0.5) == pytest.approx(150.0)
    assert imp.level(1.0) == pytest.approx(200.0)  # 1 à l'origine


# ------------------------------------------------------- scénario haussier ---


def test_impulsion_qualifiee_creation() -> None:
    states = fib_states(_mk(BASE), PARAMS)
    assert all(s is None for s in states[:29])
    state = states[29]
    assert state is not None
    assert state.status == ACTIVE
    assert state.impulse.direction == "bullish"
    assert state.impulse.confirmed_at == 29
    assert state.impulse.swing_low == pytest.approx(95.5)
    assert state.impulse.swing_high == pytest.approx(131.0)
    assert state.zone_bottom == pytest.approx(106.15)  # niveau 0.70
    assert state.zone_top == pytest.approx(113.25)  # niveau 0.50
    assert state.retests == 0
    assert state.depth == pytest.approx((131.0 - 126.0) / 35.5)


def test_amplitude_insuffisante_aucun_fib() -> None:
    params = FibParams(atr_len=2, min_amplitude_atr=50.0, disp_mult=1.0)
    states = fib_states(_mk(BASE + CONSUMPTION_TAIL), params)
    assert all(s is None for s in states)


def test_retests_comptes_par_episode_puis_consommation() -> None:
    states = fib_states(_mk(BASE + CONSUMPTION_TAIL), PARAMS)
    assert states[30] is not None and states[30].retests == 0
    assert states[31] is not None and states[31].retests == 1  # épisode 1
    assert states[32] is not None and states[32].retests == 1  # même épisode
    assert states[32].in_band is True
    assert states[33] is not None and states[33].retests == 1  # sortie
    assert states[33].in_band is False
    assert states[34] is not None and states[34].retests == 2  # épisode 2
    assert states[35] is not None and states[35].retests == 2
    assert states[36] is not None and states[36].retests == 2  # sortie
    assert states[37] is not None and states[37].retests == 3  # épisode 3
    assert states[37].status == CONSUMED
    assert states[38] is None


def test_invalidation_cloture_au_dela_du_niveau_1() -> None:
    states = fib_states(_mk(BASE + INVALIDATION_TAIL), PARAMS)
    assert states[32] is not None and states[32].status == ACTIVE
    assert states[33] is not None and states[33].status == INVALIDATED
    assert states[34] is None


def test_expiration_apres_200_bougies() -> None:
    tail = [(120.0, 120.4, 119.6, 120.0)] * 205  # plat, hors bande
    states = fib_states(_mk(BASE + tail), PARAMS)
    assert states[29 + 200] is not None and states[29 + 200].status == ACTIVE
    assert states[29 + 201] is not None and states[29 + 201].status == EXPIRED
    assert states[29 + 202] is None


def test_replacement_par_nouvelle_impulsion() -> None:
    states = fib_states(_mk(BASE + REPLACEMENT_TAIL), PARAMS)
    # L'ancien Fib (confirmé en 29) survit jusqu'à la bougie 45 : 2 épisodes
    # de contact avec sa bande (bougies 31-33 puis 39-40, hautes 112/117 >=
    # 106.15), sans jamais être invalidé (clôtures > 95.5).
    assert states[45] is not None
    assert states[45].impulse.confirmed_at == 29
    assert states[45].retests == 2
    # Remplacement en 46 par l'impulsion 97.0 -> 140.0.
    new = states[46]
    assert new is not None
    assert new.status == ACTIVE
    assert new.impulse.confirmed_at == 46
    assert new.impulse.swing_low == pytest.approx(97.0)
    assert new.impulse.swing_high == pytest.approx(140.0)
    assert new.zone_bottom == pytest.approx(140.0 - 0.7 * 43.0)
    assert new.zone_top == pytest.approx(140.0 - 0.5 * 43.0)
    assert new.retests == 0  # compteur remis à zéro


# --------------------------------------------------------- miroir baissier ---


def test_miroir_baissier_symetrie_exacte() -> None:
    bullish = fib_states(_mk(BASE + CONSUMPTION_TAIL), PARAMS)
    bearish = fib_states(_mirror(_mk(BASE + CONSUMPTION_TAIL)), PARAMS)
    state = bearish[29]
    assert state is not None
    assert state.impulse.direction == "bearish"
    assert state.status == ACTIVE
    assert state.impulse.swing_high == pytest.approx(MIRROR - 95.5)
    assert state.impulse.swing_low == pytest.approx(MIRROR - 131.0)
    # Bande miroir : [MIRROR - 113.25, MIRROR - 106.15].
    assert state.zone_bottom == pytest.approx(MIRROR - 113.25)
    assert state.zone_top == pytest.approx(MIRROR - 106.15)
    assert state.retests == bullish[29].retests
    assert bearish[37] is not None and bearish[37].status == CONSUMED
    assert bearish[38] is None


# --------------------------------------------------------- anti-lookahead ---


def test_propriete_de_prefixe() -> None:
    """Série quasi aléatoire (segments monotones + jitter) : le Fib par bougie
    ne doit jamais dépendre des bougies postérieures à la coupure."""
    rng = random.Random(1)
    price = 100.0
    candles: list[Candle] = []
    i = 0
    n = 500
    while len(candles) < n:
        if rng.random() < 0.55:  # segment impulsif (6-10 bougies, sens tiré)
            step = rng.uniform(1.5, 3.0) * rng.choice((-1.0, 1.0))
            k = rng.randint(6, 10)
        else:  # contre-mouvement court
            step = rng.uniform(-0.8, 0.8)
            k = rng.randint(3, 6)
        for _ in range(k):
            if len(candles) >= n:
                break
            new = price + step * rng.uniform(0.7, 1.3)
            # Mèches jitterées : sans jitter, open == close précédente rend les
            # highs de jonction égaux et aucune fractale stricte ne se forme.
            candles.append(
                Candle(
                    open_time=i * TF_MS,
                    close_time=i * TF_MS + TF_MS - 1,
                    open=price,
                    high=max(price, new) + rng.uniform(0.2, 0.9),
                    low=min(price, new) - rng.uniform(0.2, 0.9),
                    close=new,
                    volume=10.0,
                )
            )
            price = new
            i += 1
    params = FibParams(atr_len=3, min_amplitude_atr=0.5, disp_mult=1.0)
    full = fib_states(candles, params)
    assert any(s is not None for s in full), "la série doit produire des Fib"
    for t in range(20, len(candles), 17):
        prefix = fib_states(candles[: t + 1], params)
        assert prefix == full[: t + 1], f"préfixe rompu à la bougie {t}"


# ------------------------------------------------------------------ grade ---


def _state(
    status: str = ACTIVE,
    direction: str = "bullish",
    zone_bottom: float = 102.0,
    zone_top: float = 110.0,
) -> FibState:
    imp = FibImpulse(direction, 0, 5, 5, swing_low=90.0, swing_high=130.0)
    return FibState(
        index=5,
        status=status,
        impulse=imp,
        retests=0,
        in_band=False,
        zone_bottom=zone_bottom,
        zone_top=zone_top,
        depth=0.4,
    )


def test_ob_fib_grade_inclusion_overlap_proximite_none() -> None:
    state = _state()  # bande [102, 110], direction bullish
    assert ob_fib_grade("bullish", 103.0, 109.0, state, 4.0) == "inclusion"
    assert ob_fib_grade("bullish", 99.0, 105.0, state, 4.0) == "overlap"
    assert ob_fib_grade("bullish", 95.0, 101.0, state, 4.0) == "proximity"  # gap 1 <= 0.5 x 4
    assert ob_fib_grade("bullish", 60.0, 90.0, state, 4.0) == "none"  # gap 12
    assert ob_fib_grade("bearish", 103.0, 109.0, state, 4.0) == "none"  # sens opposé
    assert ob_fib_grade("bullish", 103.0, 109.0, None, 4.0) == "none"  # pas de Fib
    assert ob_fib_grade("bullish", 103.0, 109.0, _state(status=CONSUMED), 4.0) == "none"
    assert ob_fib_grade("bullish", 103.0, 109.0, state, None) == "none"  # ATR non défini
