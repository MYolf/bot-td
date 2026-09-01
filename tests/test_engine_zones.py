"""Tests des zones structurelles : displacement, FVG, Order Blocks.

Mêmes garanties que le reste de la famille structure : définitions
objectives vérifiées sur des séries construites à la main, et propriété de
PRÉFIXE (anti-lookahead) — pour les zones suivies dans le temps, les champs
de suivi ne peuvent différer que par des valeurs POSTÉRIEURES à la coupure.
"""

from __future__ import annotations

import random
from dataclasses import fields

from engine.strategy import Candle
from engine.zones import displacements, fair_value_gaps, order_blocks

TF_MS = 900_000


def _mk(ohlcs: list[tuple[float, float]]) -> list[Candle]:
    """Bougies dérivées d'une liste (open, close) ; h/l englobants ± 0.5."""
    return [
        Candle(
            open_time=i * TF_MS,
            close_time=i * TF_MS + TF_MS - 1,
            open=o,
            high=max(o, c) + 0.5,
            low=min(o, c) - 0.5,
            close=c,
            volume=10.0,
        )
        for i, (o, c) in enumerate(ohlcs)
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


# ----------------------------------------------------------- displacement --


def test_displacement_emis_une_fois_par_run() -> None:
    ohlcs = [(100.0, 100.0)] * 8 + [(100.0, 105.0), (105.0, 112.0)]
    events = displacements(_mk(ohlcs), atr_len=2, mult=1.0, max_bars=3)
    # Un seul événement pour le run de 2 bougies (pas de doublon en 9).
    assert len(events) == 1
    event = events[0]
    assert event.index == 8
    assert event.start == 8
    assert event.direction == "bullish"
    assert event.move == 5.0
    assert event.atr_multiple >= 1.0


def test_displacement_sens_inverse() -> None:
    ohlcs = [(100.0, 100.0)] * 8 + [(100.0, 95.0), (95.0, 87.0)]
    events = displacements(_mk(ohlcs), atr_len=2, mult=1.0, max_bars=3)
    assert len(events) == 1
    assert events[0].direction == "bearish"


# ------------------------------------------------------------------- FVG ---


def test_fvg_creation_retest_remplissage_total() -> None:
    candles = [
        _candle(0, 110.0, 110.0, 100.0, 100.0),  # bougie 2 du pattern (haut 110)
        _candle(1, 100.0, 130.0, 100.0, 130.0),  # bougie centrale (displacement)
        _candle(2, 130.0, 133.0, 131.0, 132.0),  # low 131 > high 110 -> gap
        _candle(3, 128.0, 129.0, 125.0, 128.0),  # retest : low 125 <= 131
        _candle(4, 128.0, 130.0, 100.0, 112.0),  # remplissage total : low <= 110
    ]
    # min_size_atr = 0.1 écarte aussi le micro-gap bearish né en 4
    # (high 130 < low 131, taille 1 < 0.1 x ATR ~ 16.5).
    gaps = fair_value_gaps(candles, atr_len=2, min_size_atr=0.1, disp_mult=1.0)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.index == 2
    assert gap.direction == "bullish"
    assert gap.bottom == 110.0
    assert gap.top == 131.0
    assert gap.first_retest_index == 3
    assert gap.invalidated_at == 4
    # Le gap est né du displacement de la bougie 1 (ou du run finissant en 2).
    assert gap.with_displacement is True


def test_fvg_trop_petit_ignorer() -> None:
    candles = [
        _candle(0, 110.0, 110.0, 109.0, 109.5),
        _candle(1, 109.5, 111.0, 109.0, 110.5),
        _candle(2, 110.5, 112.0, 110.2, 111.5),  # gap 110.2 - 110 = 0.2
        _candle(3, 111.5, 112.0, 110.0, 111.0),
    ]
    # Seuil : 0.25 x ATR ; ATR ~ 1.4 -> gap minimum ~ 0.35 > 0.2.
    gaps = fair_value_gaps(candles, atr_len=2, min_size_atr=0.25, disp_mult=1.5)
    assert gaps == []


# ----------------------------------------------------------- order block ---


def test_order_block_complet() -> None:
    ohlcs = [
        (100.0, 100.0),  # 0
        (100.0, 100.0),  # 1
        (100.0, 100.0),  # 2
        (100.0, 104.0),  # 3 : swing high (h = 104.5)
        (103.5, 100.0),  # 4 (h = 104.0 < 104.5)
        (100.0, 100.0),  # 5 : confirme le swing high 3 (k = 2)
        (101.0, 99.0),  # 6 : bougie bearish -> OB (zone 98.5 / 101.5)
        (99.0, 106.0),  # 7 : displacement + BOS bullish (clôture 106 > 104.5)
        (106.0, 102.0),  # 8 : retest (low 101 <= 101.5), pas de mitigation
        (102.0, 98.0),  # 9 : clôture 98 < 98.5 -> invalidation
    ]
    candles = _mk(ohlcs)
    blocks = order_blocks(
        candles, swing_k=2, atr_len=2, disp_mult=1.0, ob_lookback=5, expiry_bars=100
    )
    assert len(blocks) == 1
    block = blocks[0]
    assert block.index == 6
    assert block.direction == "bullish"
    assert block.bottom == 98.5
    assert block.top == 101.5
    assert block.bos_index == 7
    assert block.displacement_start == 7
    assert block.first_retest_index == 8
    assert block.first_mitigation_index is None  # low 101 > seuil 100.0
    assert block.invalidated_at == 9


def test_order_block_sans_displacement_pas_de_zone() -> None:
    # Même scénario, mais la cassure est graduelle : pas de displacement
    # au seuil => pas d'Order Block (évite les zones partout).
    ohlcs = [
        (100.0, 100.0),  # 0
        (100.0, 100.0),  # 1
        (100.0, 100.0),  # 2
        (100.0, 104.0),  # 3 : swing high
        (103.5, 100.0),  # 4
        (100.0, 100.0),  # 5
        (101.0, 100.0),  # 6 : doji/bearish léger
        (100.0, 101.5),  # 7 : cassure faible (clôture 101.5 < 104.5)...
        (101.5, 105.0),  # 8 : clôture 105 > 104.5 -> BOS mais move lent
        (105.0, 106.0),  # 9
    ]
    candles = _mk(ohlcs)
    blocks = order_blocks(
        candles, swing_k=2, atr_len=2, disp_mult=1.5, ob_lookback=5, expiry_bars=100
    )
    assert blocks == []


# ------------------------------------------------------ propriété de préfixe


def _random_candles(n: int) -> list[Candle]:
    rng = random.Random(123)
    price = 100.0
    candles: list[Candle] = []
    for i in range(n):
        o = price
        c = price + rng.uniform(-2.0, 2.0)
        price = c
        candles.append(
            Candle(
                open_time=i * TF_MS,
                close_time=i * TF_MS + TF_MS - 1,
                open=o,
                high=max(o, c) + rng.uniform(0.1, 2.0),
                low=min(o, c) - rng.uniform(0.1, 2.0),
                close=c,
                volume=rng.uniform(5.0, 50.0),
            )
        )
    return candles


def _tracked_field_names() -> dict[str, set[str]]:
    return {
        "FvgGap": {"first_retest_index", "invalidated_at", "expired"},
        "OrderBlock": {"first_retest_index", "first_mitigation_index", "invalidated_at", "expired"},
    }


def test_propriete_de_prefixe_zones() -> None:
    candles = _random_candles(160)
    ref_disp = displacements(candles, atr_len=3, mult=1.0, max_bars=3)
    ref_gaps = fair_value_gaps(candles, atr_len=3, min_size_atr=0.1, disp_mult=1.0)
    ref_blocks = order_blocks(candles, swing_k=2, atr_len=3, disp_mult=1.0)
    tracked = _tracked_field_names()

    for cut in (50, 110, 159):
        prefix = candles[: cut + 1]
        # Événements instantanés : identiques et complets jusqu'à la coupure.
        assert displacements(prefix, atr_len=3, mult=1.0, max_bars=3) == [
            e for e in ref_disp if e.index <= cut
        ]
        # Zones : champs de création identiques ; champs de suivi soit égaux,
        # soit remplis uniquement par des bougies postérieures à la coupure.
        by_name = {
            "gaps": (
                fair_value_gaps(prefix, atr_len=3, min_size_atr=0.1, disp_mult=1.0),
                [g for g in ref_gaps if g.index <= cut],
                "FvgGap",
            ),
            "blocks": (
                order_blocks(prefix, swing_k=2, atr_len=3, disp_mult=1.0),
                [b for b in ref_blocks if b.bos_index <= cut],
                "OrderBlock",
            ),
        }
        for partial, full, name in by_name.values():
            assert len(partial) == len(full)
            for p, f in zip(partial, full):
                for field in fields(p):
                    value_p = getattr(p, field.name)
                    value_f = getattr(f, field.name)
                    if field.name in tracked[name]:
                        # Le préfixe ne peut pas connaître l'après-coupure.
                        if field.name == "expired":
                            assert (not value_p) or value_f
                            continue
                        assert (
                            value_p == value_f
                            or (value_p is None and value_f is not None and value_f > cut)
                        ), f"{name}.{field.name} à cut={cut}"
                    else:
                        assert value_p == value_f
