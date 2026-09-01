"""Tests de l'étude d'événements OB x Fibonacci (Phase 32, étape 2).

Fonctions pures uniquement (plan étape 2) : forward return ajusté du sens,
buckets de profondeur, arithmétique R brut/net, stats de groupe, critère §7,
et une intégration légère de ``build_events`` (jointure par index cohérente
avec ``confluence_signals`` et recalcul indépendant des forward returns).
"""

from __future__ import annotations

import random

import pytest

from engine.confluence import ConfluenceParams, confluence_signals
from engine.fib_study import (
    HORIZONS,
    StudyEvent,
    build_events,
    criterion,
    depth_bucket,
    forward_return,
    group_stats,
    r_net_stats,
)
from engine.strategy import Candle

TF_MS = 900_000


def _event(
    index: int = 0,
    action: str = "BUY",
    group: str = "ob_fib",
    grade: str = "inclusion",
    depth: float | None = 0.6,
    fwd16: float | None = 0.02,
    risk_frac: float | None = 0.01,
) -> StudyEvent:
    return StudyEvent(
        index=index,
        trigger="ob_retest",
        action=action,
        group=group,
        grade=grade,
        depth=depth,
        open_time=index * TF_MS,
        risk_frac=risk_frac,
        fwd={4: None, 16: fwd16, 48: None},
    )


# --------------------------------------------------------- forward return --


def test_forward_return_ajuste_du_sens():
    closes = [100.0, 110.0, 99.0]
    # BUY : +10 % puis -1 %
    assert forward_return(closes, 0, "BUY", 1) == pytest.approx(0.10)
    assert forward_return(closes, 0, "BUY", 2) == pytest.approx(-0.01)
    # SELL : exactement l'opposé
    assert forward_return(closes, 0, "SELL", 1) == pytest.approx(-0.10)
    assert forward_return(closes, 0, "SELL", 2) == pytest.approx(0.01)
    # Horizon au-delà de la fin : None
    assert forward_return(closes, 1, "BUY", 2) is None
    assert forward_return(closes, 2, "SELL", 1) is None


# ------------------------------------------------------------ depth bucket --


@pytest.mark.parametrize(
    ("depth", "expected"),
    [
        (None, "na"),
        (-0.2, "<0.5"),
        (0.4999, "<0.5"),
        (0.5, "0.5-0.7"),  # frontière incluse côté bande
        (0.618, "0.5-0.7"),
        (0.6999, "0.5-0.7"),
        (0.7, "0.7-1.0"),  # borne haute de la bande
        (0.9, "0.7-1.0"),
        (1.0, "0.7-1.0"),
        (1.0001, ">1.0"),
        (2.5, ">1.0"),
    ],
)
def test_depth_bucket_frontieres(depth, expected):
    assert depth_bucket(depth) == expected


# ------------------------------------------------------------- r_net_stats --


def test_r_net_stats_brut_et_net():
    # (fwd - frais) / risque : 0.02 / 0.01 = 2R brut ; (0.02 - 0.0012) / 0.01 = 1.88R
    events = [_event(fwd16=0.02, risk_frac=0.01)]
    stats = r_net_stats(events, fee_rt=0.0012)
    assert stats["n"] == 1
    assert stats["mean_r"] == pytest.approx(2.0)
    assert stats["mean_net_r"] == pytest.approx(1.88)


def test_r_net_stats_moyenne_plusieurs_evenements():
    events = [
        _event(fwd16=0.02, risk_frac=0.01),  # brut 2.00R, net 1.88R
        _event(fwd16=0.01, risk_frac=0.005),  # brut 2.00R, net 1.76R
        _event(fwd16=0.0, risk_frac=0.01),  # brut 0.00R, net -0.12R
    ]
    stats = r_net_stats(events, fee_rt=0.0012)
    assert stats["n"] == 3
    assert stats["mean_r"] == pytest.approx((2.0 + 2.0 + 0.0) / 3)
    assert stats["mean_net_r"] == pytest.approx((1.88 + 1.76 - 0.12) / 3)


def test_r_net_stats_ignore_horizon_absent_et_risque_nul():
    events = [
        _event(fwd16=None, risk_frac=0.01),  # pas de forward -> exclu
        _event(fwd16=0.02, risk_frac=0.0),  # risque nul -> exclu
        _event(fwd16=0.02, risk_frac=None),  # risque inconnu -> exclu
    ]
    stats = r_net_stats(events, fee_rt=0.0012)
    assert stats == {"n": 0, "mean_r": None, "mean_net_r": None}


# ------------------------------------------------------------- group_stats --


def test_group_stats_exact():
    events = [
        _event(fwd16=0.02),
        _event(fwd16=-0.01),
        _event(fwd16=0.04),
        _event(fwd16=None),  # exclu
    ]
    stats = group_stats(events, 16)
    assert stats["n"] == 3
    assert stats["mean"] == pytest.approx(0.05 / 3)
    assert stats["median"] == pytest.approx(0.02)
    assert stats["win"] == pytest.approx(2 / 3)


def test_group_stats_vide():
    assert group_stats([], 16) == {"n": 0, "mean": None, "median": None, "win": None}
    assert group_stats([_event(fwd16=None)], 16)["n"] == 0


# --------------------------------------------------------------- criterion --


def _w(n: int, mean: float | None, r_net: float | None) -> dict:
    return {"n": n, "mean": mean, "r_net": r_net}


def test_criteres_cas_passant():
    verdict = criterion(_w(40, 0.002, 1.0), _w(35, 0.001, 0.5))
    assert verdict == {"n_min_30": True, "delta_mean_h16": True, "delta_r_net": True}


def test_criteres_n_insuffisant():
    verdict = criterion(_w(29, 0.002, 1.0), _w(50, 0.001, 0.5))
    assert verdict["n_min_30"] is False
    assert verdict["delta_mean_h16"] is True  # les deltas restent évalués
    assert verdict["delta_r_net"] is True


def test_criteres_deltas_negatifs_ou_nuls():
    verdict = criterion(_w(40, 0.001, 0.5), _w(35, 0.002, 1.0))
    assert verdict["n_min_30"] is True
    assert verdict["delta_mean_h16"] is False
    assert verdict["delta_r_net"] is False
    # égalité parfaite = delta NON strictement positif
    tied = criterion(_w(40, 0.001, 0.5), _w(35, 0.001, 0.5))
    assert tied["delta_mean_h16"] is False
    assert tied["delta_r_net"] is False


def test_criteres_donnees_manquantes():
    verdict = criterion(_w(40, None, None), _w(35, 0.001, 0.5))
    assert verdict["n_min_30"] is True
    assert verdict["delta_mean_h16"] is False
    assert verdict["delta_r_net"] is False


# --------------------------------------- intégration légère de build_events --


def _random_candles(seed: int, n: int) -> list[Candle]:
    """Marche aléatoire déterministe, assez chaotique pour des swings/BOS."""
    rng = random.Random(seed)
    price = 100.0
    out: list[Candle] = []
    for i in range(n):
        open_ = price
        close = open_ + rng.uniform(-1.5, 1.5)
        high = max(open_, close) + rng.uniform(0.0, 0.6)
        low = min(open_, close) - rng.uniform(0.0, 0.6)
        out.append(
            Candle(
                open_time=i * TF_MS,
                close_time=(i + 1) * TF_MS - 1,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=1000.0,
            )
        )
        price = close
    return out


def test_build_events_jointure_coherente():
    candles = _random_candles(seed=32, n=600)
    closes = [c.close for c in candles]
    events = build_events(candles, None)
    expected = [
        s
        for s in confluence_signals(candles, None, ConfluenceParams())
        if s.trigger == "ob_retest"
    ]
    # Un évènement par signal ob_retest, même ordre (signaux triés par index).
    assert [e.index for e in events] == [s.index for s in expected]
    assert len(events) > 0  # la série produit bien des retests d'OB
    for event, signal in zip(events, expected):
        assert event.action == signal.action
        assert event.open_time == candles[signal.index].open_time
        # Groupe cohérent avec le grade (définition §4-5).
        assert (event.group == "ob_fib") == (event.grade in ("inclusion", "overlap"))
        # Forward returns recalculés indépendamment, ajustés du sens.
        for h in HORIZONS:
            assert event.fwd[h] == forward_return(closes, event.index, event.action, h)
        # risk_frac = bracket du signal.
        assert event.risk_frac == pytest.approx(
            abs(signal.entry - signal.stop_loss) / signal.entry
        )


def test_build_events_serie_trop_courte():
    candles = _random_candles(seed=7, n=10)
    assert build_events(candles, None) == []
