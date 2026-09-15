"""Tests du stade GATE 0 (engine/liquidity_study.py) — hors ligne.

Même fixture 4H que tests/test_liquidity_engine.py : plateau 100, swing low
98 à l'index 10 (pool SSL), swing high 108 à l'index 20 (pool BSL), sweep SSL
à l'index 40 (low 97.6, close 100.5).
"""

from __future__ import annotations

import pytest

from engine.liquidity.primitives import H4
from engine.liquidity_study import (
    SweepEventStudy,
    baseline_forward_returns,
    event_forward_returns,
    forward_return_pct,
    gate0_pass,
    sens_adjust,
    sweep_events,
)
from engine.strategy import Candle


def mk4(i: int, o: float, h: float, l: float, cl: float) -> Candle:
    return Candle(i * H4, i * H4 + H4 - 1, o, h, l, cl, 1000.0)


def base_4h(n: int = 60, overrides: dict | None = None) -> list[Candle]:
    out = []
    for i in range(n):
        o = cl = 100.0
        h, l = 100.2, 99.8
        if i == 10:
            l = 98.0
        if i == 20:
            h = 108.0
        out.append(mk4(i, o, h, l, cl))
    out[40] = mk4(40, 100.0, 100.2, 97.6, 100.5)  # sweep SSL
    for i, (o, h, l, cl) in (overrides or {}).items():
        out[i] = mk4(i, o, h, l, cl)
    return out


# ------------------------------------------------------------ sweep_events


class TestSweepEvents:
    def test_detection_unique_du_sweep(self):
        events = sweep_events(base_4h(), "BTCUSDT")
        assert len(events) == 1
        e = events[0]
        assert e.index == 40
        assert e.side == "SSL"
        assert e.direction == "long"
        assert e.pool == 98.0
        assert e.close_ms == 41 * H4

    def test_range_invalide_pas_devenement(self):
        # Clôture 4H hors du range à t_s - 1 : le sweep n'est pas détecté.
        candles = base_4h(overrides={39: (100.0, 111.0, 99.8, 111.0)})
        assert sweep_events(candles, "BTCUSDT") == []

    def test_anti_re_sweep_un_seul_evenement_par_niveau(self):
        # Deux bougies consécutives percent le même pool : un seul événement.
        candles = base_4h(overrides={41: (100.0, 100.2, 97.5, 100.0)})
        events = sweep_events(candles, "BTCUSDT")
        assert [e.index for e in events] == [40]

    def test_sweep_bsl_cible_short(self):
        candles = base_4h(overrides={45: (105.0, 108.5, 104.0, 106.0)})
        events = sweep_events(candles, "BTCUSDT")
        kinds = [(e.side, e.direction) for e in events]
        assert ("BSL", "short") in kinds

    def test_propriete_de_prefixe(self):
        candles = base_4h()
        full = sweep_events(candles, "X")
        for t in (15, 30, 41, 50):
            part = sweep_events(candles[: t + 1], "X")
            assert part == [e for e in full if e.index <= t]


# -------------------------------------------------------- forward returns


class TestForwardReturns:
    def test_forward_return_et_sens(self):
        candles = base_4h()
        # Base = close du sweep (100.5) : plateau 100 -> léger négatif.
        assert forward_return_pct(candles, 40, 12) == pytest.approx(
            (100.0 / 100.5 - 1.0) * 100.0
        )
        candles[52] = mk4(52, 100.0, 100.2, 99.8, 110.0)
        assert forward_return_pct(candles, 40, 12) == pytest.approx(
            (110.0 / 100.5 - 1.0) * 100.0
        )
        assert sens_adjust(10.0, "long") == 10.0
        assert sens_adjust(10.0, "short") == -10.0

    def test_horizon_hors_donnees_ignore(self):
        candles = base_4h(n=50)
        assert forward_return_pct(candles, 40, 12) is None

    def test_event_forward_returns_par_horizon(self):
        candles = base_4h(n=100)  # indices jusqu'à 88 (h=48 depuis 40)
        candles[52] = mk4(52, 100.0, 100.2, 99.8, 104.0)
        candles[64] = mk4(64, 100.0, 100.2, 99.8, 102.0)
        events = sweep_events(candles, "BTCUSDT")
        fr = event_forward_returns(candles, events)
        base = 100.5  # close de la bougie de sweep
        assert fr[12] == [pytest.approx((104.0 / base - 1.0) * 100.0)]
        assert fr[24] == [pytest.approx((102.0 / base - 1.0) * 100.0)]
        assert fr[48] == [pytest.approx((100.0 / base - 1.0) * 100.0)]


class TestBaseline:
    def test_slots_apparies_et_jours_evenements_exclus(self):
        # Epoch 4H : les slots heure = 0,4,8,12,16,20 ; jour = floor(i/6).
        # L'événement (index 40) porte sur le jour 6, slot heure 16, et le
        # 6 janvier 1970 est un mercredi-like (weekday 2) : la baseline
        # reprend les bougies i % 6 == 4 des jours 13 (weekday 2, sans
        # événement), soit exactement l'index 82 — le jour 6 étant exclu.
        candles = base_4h(n=120)  # 20 jours, jour 13 = indices 78..83
        events = sweep_events(candles, "BTCUSDT")
        assert [e.index for e in events] == [40]
        base = baseline_forward_returns(candles, events)
        # Plateau : tous les forward returns valent 0 %.
        assert base[12] == [pytest.approx(0.0)]  # unique bougie appariée : 82
        assert base[24] == [pytest.approx(0.0)]
        assert base[48] == []  # 82 + 48 = 130 > 119 : horizon ignoré


# ------------------------------------------------------------------ gate


class TestGate0:
    def test_effectif_insuffisant(self):
        ok, details = gate0_pass(99, {12: [5.0]}, {12: [1.0]})
        assert ok is False
        assert "FAIL" in details[-1]

    def test_passe_si_un_horizon_positif_au_dessus_de_la_baseline(self):
        fr = {12: [-1.0], 24: [0.5, 0.5, 0.5], 48: [1.0]}
        base = {12: [0.0], 24: [0.1], 48: [2.0]}
        ok, _ = gate0_pass(100, fr, base)
        assert ok is True

    def test_echoue_si_jamais_au_dessus_de_la_baseline(self):
        fr = {12: [1.0], 24: [2.0], 48: [0.5]}
        base = {12: [1.0], 24: [3.0], 48: [2.0]}
        ok, _ = gate0_pass(100, fr, base)
        assert ok is False

    def test_echoue_si_mediane_negative(self):
        ok, _ = gate0_pass(100, {12: [-1.0], 24: [-1.0], 48: [-1.0]}, {12: [-2.0]})
        assert ok is False

    def test_valeurs_manquantes_ne_passent_pas(self):
        ok, _ = gate0_pass(100, {12: [1.0]}, {12: []})
        assert ok is False
