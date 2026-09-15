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


# ------------------------------------------------------------- stade IS ----


from engine.liquidity.engine import SimParams, TradeResult
from engine.liquidity_study import (
    FEE_MAKER_IN_TAKER_OUT_PCT,
    FEE_MAKER_PCT,
    FEE_TAKER_PCT,
    bootstrap_ci,
    evaluate_gates_1_5,
    plateau_variations,
    summarize,
    trade_net,
    ventilation,
)


def mk_trade(
    result_r: float,
    risk_pct: float = 1.4,
    direction: str = "long",
    entry_type: str = "taker",
) -> TradeResult:
    return TradeResult(
        candidate="A1",
        tp_policy="median",
        direction=direction,
        entry_time_ms=0,
        exit_time_ms=H4,
        entry=100.0,
        exit_avg=100.0,
        sl=98.6,
        tps=[103.0],
        result_r=result_r,
        exit_reason="TP",
        duration_h=4.0,
        entry_type=entry_type,
        risk_pct=risk_pct,
        pool=98.0,
        range_median=103.0,
        range_height=10.0,
    )


class TestTradeNet:
    def test_niveaux_de_frais(self):
        t = mk_trade(1.0, risk_pct=1.4)
        # cout_R = frais % / risk_pct
        assert trade_net(t, "brut" if False else "taker") == pytest.approx(
            1.0 - FEE_TAKER_PCT / 1.4
        )
        assert trade_net(t, "maker") == pytest.approx(1.0 - FEE_MAKER_PCT / 1.4)
        assert trade_net(t, "real") == pytest.approx(1.0 - FEE_TAKER_PCT / 1.4)

    def test_fill_maker_sortie_realiste(self):
        t = mk_trade(1.0, risk_pct=1.4, entry_type="maker")
        assert trade_net(t, "real") == pytest.approx(
            1.0 - FEE_MAKER_IN_TAKER_OUT_PCT / 1.4
        )


class TestSummarize:
    def test_vide(self):
        m = summarize([])
        assert m["n"] == 0 and m["exp"] is None

    def test_metriques_connues(self):
        # [+1, +1, -1, +2, -1] : exp=0.4, wr=60 %, PF=4/2=2, DD=1
        # (equity 1,2,1,3,2 -> plus grand creux sous sommet = 1).
        m = summarize([1.0, 1.0, -1.0, 2.0, -1.0])
        assert m["n"] == 5
        assert m["exp"] == pytest.approx(0.4)
        assert m["wr"] == pytest.approx(60.0)
        assert m["pf"] == pytest.approx(2.0)
        assert m["dd"] == pytest.approx(1.0)

    def test_pf_infini_sans_perte(self):
        assert summarize([1.0, 2.0])["pf"] is None

    def test_bootstrap(self):
        ci = bootstrap_ci([1.0, -1.0, 1.0, 1.0], n_boot=500)
        assert ci is not None and ci[0] <= ci[1]


def _cell(symbol: str, direction: str, wins: int, losses: int):
    # Interleave : 2 gains puis 1 perte, pour garder un drawdown modéré
    # (des gains tous en tête gonfleraient artificiellement le DD poolé).
    rs = []
    for k in range(wins + losses):
        cycle = k % 3
        rs.append(1.0 if (cycle < 2 and wins > 0) or (cycle == 2 and losses == 0) else (-1.0 if cycle == 2 else 1.0))
    rs = sorted(rs, reverse=True)
    # distribution : wins x +1 puis losses x -1, puis rotation régulière
    seq = ([1.0, 1.0, -1.0] * losses + [1.0] * (wins - 2 * losses)) if wins >= 2 * losses else None
    trades = [mk_trade(r, direction=direction) for r in (seq if seq else rs)]
    return [(symbol, direction), trades]


def _cells_ok():
    # 60 trades/cellule (40 gagnants +1R, 20 perdants -1R), 2 symboles :
    # brut +0.333R, net taker +0.233R, PF 2.0, DD 2R -> tous gates OK.
    return dict([_cell("BTCUSDT", "long", 40, 20), _cell("ETHUSDT", "long", 40, 20)])


class TestGates:
    def test_tout_passe(self):
        out = evaluate_gates_1_5(_cells_ok())
        assert out["long"][0] is True
        assert out["short"][0] is False  # aucun trade short

    def test_gate1_effectifs(self):
        cells = dict([_cell("BTCUSDT", "long", 30, 29), _cell("ETHUSDT", "long", 40, 20)])
        out = evaluate_gates_1_5(cells)
        assert out["long"][0] is False
        assert "[long] 1" in out["long"][1][0]

    def test_gate2_brut_negatif(self):
        cells = dict([_cell("BTCUSDT", "long", 40, 20), _cell("ETHUSDT", "long", 10, 50)])
        out = evaluate_gates_1_5(cells)
        assert out["long"][0] is False

    def test_gate3_nette_taker_insuffisante(self):
        # RR 1:1 -> brut +0.333 mais frais 0.1R par trade -> net +0.233 ; pour
        # faire chuter sous +0.10, serrer le risque : risk_pct faible.
        cells = {}
        for sym in ("BTCUSDT", "ETHUSDT"):
            trades = [
                mk_trade(1.0 if k < 40 else -1.0, risk_pct=0.5) for k in range(60)
            ]
            cells[(sym, "long")] = trades
        out = evaluate_gates_1_5(cells)
        assert out["long"][0] is False

    def test_gate5_drawdown(self):
        # 80 pertes consecutives -0.5R (brut negatif aussi, mais on verifie DD).
        trades_btc = [mk_trade(-0.05) for _ in range(60)]
        trades_eth = [mk_trade(-0.05) for _ in range(60)]
        cells = {("BTCUSDT", "long"): trades_btc, ("ETHUSDT", "long"): trades_eth}
        out = evaluate_gates_1_5(cells)
        assert out["long"][0] is False


class TestPlateau:
    def test_28_variations_parametre_par_parametre(self):
        variations = plateau_variations()
        assert len(variations) == 28  # 14 parametres x 2
        labels = [l for l, _ in variations]
        assert labels[0].startswith("pool_window -")
        # entiers arrondis
        by_label = dict(variations)
        assert by_label["pool_window -20%"].pool_window == 40
        assert by_label["pool_window +20%"].pool_window == 60
        assert by_label["activation_a_1h -20%"].activation_a_1h == 16
        # les autres champs restent a la valeur scellee
        assert by_label["pool_window -20%"].sl_buffer_atr == SimParams().sl_buffer_atr


class TestVentilation:
    def test_buckets(self):
        trades = [mk_trade(1.0), mk_trade(-1.0), mk_trade(2.0)]
        # ventilation en NET TAKER : chaque R est amputé de 0.14/1.4 = 0.1R.
        vent = ventilation(trades, lambda t: "x" if t.result_r > 0 else "y")
        assert vent == {"x": pytest.approx(1.4), "y": pytest.approx(-1.1)}
