"""Tests du moteur de simulation LIQUIDITY.md §5-§8 (engine/liquidity/engine.py).

Fixture de base (candidates A/B, 4H) :
- plateau 100 (high 100.2 / low 99.8) ; les égalités ne créent aucun swing ;
- swing low à l'index 10 (low 98) -> pool SSL = 98 (confirmé à 13) ;
- swing high à l'index 20 (high 108) -> pool BSL = 108 (confirmé à 23) ;
- range valide dès t = 23 (largeur 10 >= 5 x ATR14 ~= 0.55) ;
- sweep SSL à l'index 40 : low 97.6, close 100.5 (percé puis rejeté).

Le sweep 4H #40 clôture à 41 x H4 : la 1H #163 clôture au même instant (elle
n'est JAMAIS éligible), la 1H #164 (open 41 x H4) est la première éligible.
"""

from __future__ import annotations

import pytest

from engine.liquidity.engine import SEALED_CONFIGS, LiquiditySimulator, SimConfig
from engine.liquidity.primitives import H1, H4, atr14_4h
from engine.strategy import Candle


def mk4(i: int, o: float, h: float, l: float, cl: float) -> Candle:
    return Candle(i * H4, i * H4 + H4 - 1, o, h, l, cl, 1000.0)


def mk1(j: int, o: float, h: float, l: float, cl: float) -> Candle:
    return Candle(j * H1, j * H1 + H1 - 1, o, h, l, cl, 1000.0)


def base_4h(n: int = 60, bsl: float = 108.0, overrides: dict | None = None) -> list[Candle]:
    out = []
    for i in range(n):
        o = cl = 100.0
        h, l = 100.2, 99.8
        if i == 10:
            l = 98.0
        if i == 20:
            h = bsl
        out.append(mk4(i, o, h, l, cl))
    out[40] = mk4(40, 100.0, 100.2, 97.6, 100.5)  # sweep SSL
    for i, (o, h, l, cl) in (overrides or {}).items():
        out[i] = mk4(i, o, h, l, cl)
    return out


def base_1h(n: int = 300, overrides: dict | None = None) -> list[Candle]:
    out = [mk1(j, 100.0, 100.2, 99.8, 100.0) for j in range(n)]
    for j, (o, h, l, cl) in (overrides or {}).items():
        out[j] = mk1(j, o, h, l, cl)
    return out


# 1H #164-165 : close 97.5 (pas de déclencheur long) ; #166 : close 100.5 > 98.
DECL_A1 = {
    164: (100.0, 100.2, 99.3, 97.5),
    165: (100.0, 100.2, 99.3, 97.5),
    166: (100.0, 100.5, 99.8, 100.5),
}


def run_1h(
    config: SimConfig, c1: list[Candle], c4: list[Candle] | None = None
) -> list:
    sim = LiquiditySimulator(c4 if c4 is not None else base_4h(), c1, config)
    return sim.run()


# ------------------------------------------------------------ SimConfig


class TestSimConfig:
    def test_les_6_combinaisons_sont_acceptees(self):
        for cand, pol in SEALED_CONFIGS:
            assert SimConfig(cand, pol).candidate == cand

    @pytest.mark.parametrize(
        "cand,pol", [("A1", "swing"), ("B", "median"), ("C", "50/50"), ("X", "median")]
    )
    def test_combinaison_non_scellee_rejetee(self, cand, pol):
        with pytest.raises(ValueError, match="non scellée"):
            SimConfig(cand, pol)

    def test_bougies_non_alignees_rejetees(self):
        bad = [Candle(1234, 1234 + H1 - 1, 1.0, 1.0, 1.0, 1.0, 1.0)]
        with pytest.raises(ValueError, match="align"):
            LiquiditySimulator(base_4h(), bad, SimConfig("A1", "median"))


# ------------------------------------------------------------ Candidate A1


class TestA1Median:
    def test_entree_au_declencheur_puis_tp_median(self):
        c1 = base_1h(300, {**DECL_A1, 167: (100.5, 103.5, 100.0, 103.0)})
        trades = run_1h(SimConfig("A1", "median"), c1)
        assert len(trades) == 1
        t = trades[0]
        atr4 = atr14_4h(base_4h())
        sl = 97.6 - 0.25 * atr4[40]
        assert t.direction == "long"
        assert t.entry == pytest.approx(100.5)
        assert t.entry_type == "taker"
        assert t.tps == [pytest.approx(103.0)]  # médiane de (98, 108)
        assert t.sl == pytest.approx(sl)
        assert t.exit_reason == "TP"
        assert t.exit_avg == pytest.approx(103.0)
        assert t.result_r == pytest.approx((103.0 - 100.5) / (100.5 - sl))
        # entrée à la clôture de la 1H #166 — jamais avant (anti-lookahead :
        # la 1H #163, qui clôture avec le sweep 4H, n'est pas éligible).
        assert t.entry_time_ms == 167 * H1
        assert t.pool == pytest.approx(98.0)
        assert t.range_median == pytest.approx(103.0)
        assert t.risk_pct == pytest.approx((100.5 - sl) / 100.5 * 100)

    def test_sortie_sl(self):
        c1 = base_1h(300, {**DECL_A1, 167: (100.5, 101.0, 97.0, 97.5)})
        trades = run_1h(SimConfig("A1", "median"), c1)
        assert len(trades) == 1
        t = trades[0]
        sl = 97.6 - 0.25 * atr14_4h(base_4h())[40]
        assert t.exit_reason == "SL"
        assert t.exit_avg == pytest.approx(sl)
        assert t.result_r == pytest.approx(-1.0)

    def test_sl_prioritaire_si_sl_et_tp_sur_la_meme_bougie(self):
        c1 = base_1h(300, {**DECL_A1, 167: (100.5, 104.0, 97.0, 100.0)})
        trades = run_1h(SimConfig("A1", "median"), c1)
        assert trades[0].exit_reason == "SL"
        assert trades[0].result_r == pytest.approx(-1.0)

    def test_sortie_temporelle_128h_apres_entree(self):
        # Aucun TP (103) ni SL touché après l'entrée : sortie TIME.
        c1 = base_1h(300, DECL_A1)
        trades = run_1h(SimConfig("A1", "median"), c1)
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "TIME"
        assert t.exit_avg == pytest.approx(100.0)  # close de la 1H #294
        assert t.exit_time_ms == 295 * H1  # entrée 167xH1 + 128 h
        assert t.duration_h == pytest.approx(128.0)
        sl = 97.6 - 0.25 * atr14_4h(base_4h())[40]
        assert t.result_r == pytest.approx((100.0 - 100.5) / (100.5 - sl))

    def test_sortie_eod_en_fin_de_donnees(self):
        c1 = base_1h(172, DECL_A1)  # données coupées 6 bougies après l'entrée
        trades = run_1h(SimConfig("A1", "median"), c1)
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "EOD"
        assert t.exit_avg == pytest.approx(100.0)
        assert t.exit_time_ms == 172 * H1

    def test_aucun_signal_avant_activation_1h(self):
        # La 1H #163 (open < clôture du sweep 4H) clôture à 100 > pool : si le
        # moteur l'autorisait à tort, l'entrée serait à 164 x H1.
        c1 = base_1h(300, {**DECL_A1, 163: (100.0, 100.2, 99.8, 100.0)})
        trades = run_1h(SimConfig("A1", "median"), c1)
        assert trades[0].entry_time_ms == 167 * H1


class TestA1CinquanteCinquante:
    def test_deux_jambes_mediane_et_bsl(self):
        c1 = base_1h(
            300,
            {
                **DECL_A1,
                167: (100.5, 103.5, 100.0, 103.0),  # TP1 (médiane 103)
                168: (103.0, 108.5, 102.5, 108.0),  # TP2 (BSL 108)
            },
        )
        trades = run_1h(SimConfig("A1", "50/50"), c1)
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "TP1+TP2"
        assert t.tps == [pytest.approx(103.0), pytest.approx(108.0)]
        assert t.exit_avg == pytest.approx(105.5)  # pondération 50/50
        sl = 97.6 - 0.25 * atr14_4h(base_4h())[40]
        assert t.result_r == pytest.approx((105.5 - 100.5) / (100.5 - sl))

    def test_tp1_puis_sl_sur_la_jambe_restante(self):
        # TP1 touché, puis retour au SL : la jambe 2 sort au SL -> R global
        # = 0.5 x RR1 - 0.5 x 1 (sortie partielle déjà encaissée).
        c1 = base_1h(
            300,
            {
                **DECL_A1,
                167: (100.5, 103.5, 100.0, 103.0),  # TP1
                168: (103.0, 103.2, 96.0, 96.5),  # SL (low sous le stop)
            },
        )
        trades = run_1h(SimConfig("A1", "50/50"), c1)
        assert len(trades) == 1
        t = trades[0]
        assert t.exit_reason == "TP1+SL"
        sl = 97.6 - 0.25 * atr14_4h(base_4h())[40]
        assert t.exit_avg == pytest.approx(0.5 * 103.0 + 0.5 * sl)
        assert t.result_r == pytest.approx(
            (0.5 * 103.0 + 0.5 * sl - 100.5) / (100.5 - sl)
        )


# ------------------------------------------------------------ Candidate A2


class TestA2:
    def test_fill_limite_puis_tp(self):
        # Ordre à pool + 0.25 x ATR (~98.17) : la 1H #164 le remplit sans
        # toucher le SL ; TP médiane ensuite.
        c1 = base_1h(300, {164: (100.0, 100.2, 98.0, 99.0), 165: (99.0, 103.5, 98.5, 103.0)})
        trades = run_1h(SimConfig("A2", "median"), c1)
        assert len(trades) == 1
        t = trades[0]
        atr4 = atr14_4h(base_4h())
        order = 98.0 + 0.25 * atr4[40]
        sl = 97.6 - 0.25 * atr4[40]
        assert t.entry_type == "maker"
        assert t.entry == pytest.approx(order)
        assert t.sl == pytest.approx(sl)
        assert t.exit_reason == "TP"
        assert t.exit_avg == pytest.approx(103.0)
        assert t.result_r == pytest.approx((103.0 - order) / (order - sl))
        assert t.entry_time_ms == 165 * H1  # clôture de la bougie de fill

    def test_fill_et_sl_sur_la_meme_bougie(self):
        # La bougie de fill traverse aussi le SL : convention prudente.
        c1 = base_1h(300, {164: (100.0, 100.2, 97.0, 97.5)})
        trades = run_1h(SimConfig("A2", "median"), c1)
        assert len(trades) == 1
        t = trades[0]
        assert t.entry_type == "maker"
        assert t.exit_reason == "SL"
        assert t.result_r == pytest.approx(-1.0)

    def test_tp_non_compte_sur_la_bougie_de_fill(self):
        # La bougie de fill atteint aussi la médiane (103) : le TP n'est PAS
        # compté (instant du fill inconnu) ; il est validé à la bougie suivante.
        c1 = base_1h(300, {164: (100.0, 103.5, 98.0, 103.0), 165: (103.0, 103.5, 102.5, 103.2)})
        trades = run_1h(SimConfig("A2", "median"), c1)
        assert len(trades) == 1
        assert trades[0].exit_reason == "TP"
        assert trades[0].exit_time_ms == 166 * H1  # clôture de la 1H #165

    def test_expiration_de_l_ordre_apres_20_bougies(self):
        # Les 1H par défaut (low 99.8) ne remplissent jamais l'ordre ~98.17 ;
        # 20 bougies éligibles (#164..#183) passent, l'ordre expire ; la
        # #184 descend au niveau mais il n'y a plus d'ordre.
        c1 = base_1h(300, {184: (100.0, 100.2, 98.0, 99.0)})
        trades = run_1h(SimConfig("A2", "median"), c1)
        assert trades == []

    def test_annulation_par_cloture_4h_au_dela_du_pool(self):
        # 4H #42 clôture à 97.0 (< 98 - 0.5 x ATR) : cassure franche, l'ordre
        # d'achat est annulé ; la 1H #172 redescend au niveau en vain.
        c4 = base_4h(overrides={42: (100.0, 100.2, 96.8, 97.0)})
        c1 = base_1h(300, {172: (100.0, 100.2, 98.0, 99.0)})
        trades = run_1h(SimConfig("A2", "median"), c1, c4)
        assert trades == []


# ------------------------------------------------- événements et consommations


class TestGestionEvenements:
    def test_sweep_pendant_position_ignore_mais_consomme(self):
        # Position longue ouverte (entrée 1H #166). 4H #41 = sweep BSL ->
        # ignoré (position ouverte) MAIS consommé ; 4H #43 re-sweep du même
        # pool 108 -> bloqué par l'anti-re-sweep. Sans la consommation, la
        # position close (TP à la 1H #172) laisserait le setup du #43 se
        # déclencher en short et produire un 2e trade.
        c4 = base_4h(
            overrides={
                41: (105.0, 108.5, 105.0, 106.0),  # sweep BSL (position ouverte)
                43: (105.0, 108.6, 104.0, 105.0),  # re-sweep bloqué
            }
        )
        c1 = base_1h(300, {**DECL_A1, 172: (100.0, 103.5, 100.0, 103.0)})
        trades = run_1h(SimConfig("A1", "median"), c1, c4)
        assert len(trades) == 1
        assert trades[0].direction == "long"
        assert trades[0].exit_reason == "TP"

    def test_nouvel_evenement_remplace_le_setup_en_attente(self):
        # Setup long ouvert par le sweep SSL (#40) ; les 1H #164-167 clôturent
        # à 97.5 (aucun déclencheur long) ; 4H #41 = sweep BSL confirmé après
        # la 1H #167 -> le setup est REMPLACÉ par un setup short (pool 108) ;
        # la 1H #168 (close 100 < 108) déclenche le short.
        c4 = base_4h(overrides={41: (105.0, 108.5, 105.0, 106.0)})
        attente = {j: (100.0, 100.2, 99.3, 97.5) for j in range(164, 168)}
        c1 = base_1h(300, attente)
        trades = run_1h(SimConfig("A1", "median"), c1, c4)
        assert len(trades) == 1
        t = trades[0]
        assert t.direction == "short"
        assert t.pool == pytest.approx(108.0)
        assert t.entry == pytest.approx(100.0)
        assert t.entry_time_ms == 169 * H1
        # TP court = médiane 103 (AU-DESSUS de l'entrée 100 : perte
        # structurelle acceptée par la politique « médiane » figée).
        assert t.tps == [pytest.approx(103.0)]
        assert t.exit_reason == "TP"  # low 99.8 <= 103 dès la bougie suivante

    def test_fenetre_d_activation_expiree(self):
        # Aucune 1H éligible ne clôture au-dessus du pool 98 (closes 97.5) :
        # la fenêtre de 20 bougies (#164..#183) expire, puis la #190 clôture
        # à 100.5 sans effet.
        slow = {j: (100.0, 100.2, 99.3, 97.5) for j in range(164, 184)}
        c1 = base_1h(300, {**slow, 190: (100.0, 100.5, 99.8, 100.5)})
        trades = run_1h(SimConfig("A1", "median"), c1)
        assert trades == []


# ------------------------------------------------------------ Candidate B


class TestB:
    # 1H #164-166 : construction du FVG bullish au-dessus du pool 98
    # (zone [99.9, 100.0], ordre au milieu 99.95), fill à la #167, TP au
    # swing 4H confirmé (108) à la #168.
    FVG_1H = {
        164: (99.8, 99.9, 99.3, 99.5),
        165: (99.5, 100.7, 99.4, 100.5),
        166: (99.6, 101.2, 100.0, 101.0),
        167: (100.5, 101.0, 99.5, 100.0),
        168: (100.0, 108.5, 99.8, 108.0),
    }

    def test_fill_dans_le_fvg_puis_tp_swing(self):
        c1 = base_1h(300, self.FVG_1H)
        trades = run_1h(SimConfig("B", "swing"), c1)
        assert len(trades) == 1
        t = trades[0]
        atr4 = atr14_4h(base_4h())
        sl = 98.0 - 0.25 * atr4[40]  # min(zone_low, pool) - tampon
        assert t.entry_type == "maker"
        assert t.entry == pytest.approx(99.95)  # milieu de zone
        assert t.sl == pytest.approx(sl)
        assert t.tps == [pytest.approx(108.0)]  # dernier swing high 4H confirmé
        assert t.exit_reason == "TP"
        assert t.exit_avg == pytest.approx(108.0)
        assert t.result_r == pytest.approx((108.0 - 99.95) / (99.95 - sl))
        assert t.entry_time_ms == 168 * H1

    def test_trade_non_pris_si_rr_insuffisant(self):
        # BSL du range à 102 : TP potentiel 102, risque ~2.1 -> RR < 1.2,
        # le trade est refusé au fill.
        c4 = base_4h(bsl=102.0)
        c1 = base_1h(300, self.FVG_1H)
        trades = run_1h(SimConfig("B", "swing"), c1, c4)
        assert trades == []

    def test_ordre_expire_sans_fill(self):
        # Après le FVG (#166), 20 bougies sans toucher le milieu de zone
        # (low 100.0 > 99.95) ; la bougie #187 descend trop tard.
        calme = {j: (100.5, 100.7, 100.0, 100.5) for j in range(167, 187)}
        c1 = base_1h(
            300,
            {164: self.FVG_1H[164], 165: self.FVG_1H[165], 166: self.FVG_1H[166],
             **calme, 187: (100.5, 100.7, 99.5, 100.0)},
        )
        trades = run_1h(SimConfig("B", "swing"), c1)
        assert trades == []


# ------------------------------------------------------------ Candidate C


def fixture_c_4h() -> list[Candle]:
    """Rampe lente (+0.01/bougie, EMA200 croissante dès t=199) avec pools
    SSL 96 (swing low idx 170) et BSL 112 (swing high idx 190), range valide
    (largeur 16 >> 5 x ATR), puis breakout en clôture à l'idx 210."""
    out = []
    for i in range(230):
        o = 100.0 + 0.01 * i
        cl = 100.0 + 0.01 * (i + 1)
        h = cl + 0.1
        l = o - 0.1
        if i == 170:
            l = 96.0
        if i == 190:
            h = 112.0
            cl = 102.0
        out.append(mk4(i, o, h, l, cl))
    out[210] = mk4(210, 103.0, 113.5, 102.9, 113.0)
    for i in range(211, 230):
        out[i] = mk4(i, 113.0, 113.2, 112.8, 113.0)
    return out


def fixture_c_1h() -> list[Candle]:
    # Défauts neutres vers 112.5 (ni retest ni TP ni SL) ; retest à la #850,
    # TP 128 à la #860.
    out = [mk1(j, 112.5, 112.7, 112.3, 112.5) for j in range(920)]
    out[850] = mk1(850, 112.5, 113.0, 111.9, 112.6)
    out[860] = mk1(860, 112.6, 128.5, 112.5, 128.0)
    return out


class TestC:
    def test_breakout_retest_puis_tp_mesured_move(self):
        sim = LiquiditySimulator(fixture_c_4h(), fixture_c_1h(), SimConfig("C", "measured"))
        trades = sim.run()
        assert len(trades) == 1
        t = trades[0]
        atr4 = atr14_4h(fixture_c_4h())
        sl = 111.9 - 0.25 * atr4[210]  # min(low retest, pool 112) - tampon
        assert t.direction == "long"
        assert t.entry_type == "taker"
        assert t.pool == pytest.approx(112.0)
        assert t.entry == pytest.approx(112.6)  # close de la bougie de retest
        assert t.sl == pytest.approx(sl)
        assert t.tps == [pytest.approx(128.0)]  # pool + hauteur du range (16)
        assert t.exit_reason == "TP"
        assert t.exit_avg == pytest.approx(128.0)
        assert t.result_r == pytest.approx((128.0 - 112.6) / (112.6 - sl))
        # Le breakout 4H #210 clôture à 844 x H1 : la 1H #844 est la première
        # éligible ; le retest se déclenche à la #850.
        assert t.entry_time_ms == 851 * H1

    def test_pas_de_trade_avant_warmup_ema200(self):
        # Le même breakout sans le mouvement : rien avant que l'EMA200 existe.
        c4 = fixture_c_4h()[:200]
        c1 = fixture_c_1h()[:800]
        sim = LiquiditySimulator(c4, c1, SimConfig("C", "measured"))
        assert sim.run() == []
