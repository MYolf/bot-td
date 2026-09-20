"""Tests des embeds Discord (Phase 12)."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from app.discord.embeds import (
    GREEN,
    RED,
    build_closure_embed,
    build_performance_embed,
    build_signal_embed,
    build_stats_embed,
    format_day_time,
    format_price,
    format_risk_reward,
    scaled_targets,
    strategy_label,
    timeframe_label,
)
from app.paper_trading.engine import CloseOutcome
from app.paper_trading.statistics import compute_stats


def _embed(action: str = "BUY"):
    # Bracket cohérent avec la direction : BUY -> SL sous l'entrée, SELL au-dessus.
    if action == "SELL":
        stop_loss, take_profit = "105264.84", "103067.58"
    else:
        stop_loss, take_profit = "103800", "106000"
    return build_signal_embed(
        action=action,
        symbol="BTCUSDT",
        strategy="momentum_v1",
        timeframe="15",
        entry_price="104532.42",
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward="2.0",
        signal_time=datetime(2026, 8, 22, 22, 14, 3, tzinfo=timezone.utc),
    )


class TestFormat:
    def test_prix_avec_separateurs(self):
        assert format_price("104532.42") == "104,532.42"

    def test_prix_entier_sans_decimales(self):
        assert format_price("106000") == "106,000"

    def test_rr_entier(self):
        assert format_risk_reward("2.00") == "1:2"

    def test_rr_decimal(self):
        assert format_risk_reward("1.50") == "1:1.5"

    def test_timeframe(self):
        assert timeframe_label("15") == "15m"
        assert timeframe_label("60") == "1h"
        assert timeframe_label("240") == "4h"
        assert timeframe_label("D") == "1D"

    def test_strategy(self):
        assert strategy_label("momentum_v1") == "Momentum V1"


class TestEmbed:
    def test_buy_titre_vert(self):
        embed = _embed("BUY")
        assert embed.title == "🟢 LONG SIGNAL — BTCUSDT"
        assert embed.color.value == GREEN

    def test_sell_titre_rouge(self):
        embed = _embed("SELL")
        assert embed.title == "🔴 SHORT SIGNAL — BTCUSDT"
        assert embed.color.value == RED

    def test_champs_complets(self):
        embed = _embed()
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Strategy"] == "Momentum V1"
        assert champs["Timeframe"] == "15m"
        assert champs["Entry"] == "104,532.42"
        assert champs["Stop Loss"] == "103,800"
        # TP avec distance en pips (1 pip = 1 $ : 106000 - 104532.42 = 1467.58).
        assert champs["Take Profit"] == "106,000\n+1,468 pips"
        assert champs["Risk/Reward"] == "1:2"
        assert champs["Signal Time"] == "22:14:03 UTC"

    def test_sorties_partielles_buy(self):
        # Risque = 104532.42 - 103800 = 732.42 -> TP1/TP2 à +1R/+2R,
        # déclencheur BE à +1,5R (mi-chemin TP1->TP2). TP3 = ouvert, sans
        # take profit (solde laissé en gestion libre). Pips arrondis au plus
        # proche : 732.42 -> 732, 1464.84 -> 1465.
        embed = _embed("BUY")
        champs = {f.name: f.value for f in embed.fields}
        partielles = champs["Sorties partielles (suggestion)"]
        assert partielles == (
            "TP1 : 105,264.84 (+1R · +732 pips)\n"
            "TP2 : 105,997.26 (+2R · +1,465 pips)\n"
            "TP3 : ouvert\n"
            "BE : SL → entrée à 105,631.05 (+1,5R)"
        )

    def test_sorties_partielles_sell(self):
        # SELL : risque = SL - entry = 105264.84 - 104532.42 = 732.42 vers le bas.
        embed = _embed("SELL")
        champs = {f.name: f.value for f in embed.fields}
        partielles = champs["Sorties partielles (suggestion)"]
        assert partielles == (
            "TP1 : 103,800 (+1R · +732 pips)\n"
            "TP2 : 103,067.58 (+2R · +1,465 pips)\n"
            "TP3 : ouvert\n"
            "BE : SL → entrée à 103,433.79 (+1,5R)"
        )

    def test_sell_take_profit_pips(self):
        # SELL : TP sous l'entrée, distance symétrique du BUY.
        embed = _embed("SELL")
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Take Profit"] == "103,067.58\n+1,465 pips"


class TestScaledTargets:
    def test_buy_multiples_de_r(self):
        tp = scaled_targets("BUY", "100", "98")
        assert tp == (Decimal("102"), Decimal("104"), Decimal("106"))

    def test_sell_miroir(self):
        tp = scaled_targets("SELL", "100", "102")
        assert tp == (Decimal("98"), Decimal("96"), Decimal("94"))

    def test_multiples_configurables(self):
        tp = scaled_targets("BUY", "100", "99", multiples=(1, 3))
        assert tp == (Decimal("101"), Decimal("103"))

    def test_multiple_demi(self):
        # Déclencheur BE : +1,5R = mi-chemin entre TP1 (+1R) et TP2 (+2R).
        tp = scaled_targets("BUY", "100", "98", multiples=(1.5,))
        assert tp == (Decimal("103"),)

    def test_heure_convertie_en_utc(self):
        # 22:14:03 UTC+2 -> 20:14:03 UTC
        embed = build_signal_embed(
            action="BUY",
            symbol="BTCUSDT",
            strategy="momentum_v1",
            timeframe="15",
            entry_price="1",
            stop_loss="1",
            take_profit="1",
            risk_reward="1",
            signal_time=datetime.fromisoformat("2026-08-22T22:14:03+02:00"),
        )
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Signal Time"] == "20:14:03 UTC"


class TestFormatDayTime:
    def test_jour_date_heure_paris_ete(self):
        # Août : Paris à UTC+2 -> 12:00 UTC = 14:00 Paris (vendredi 28/08/2026).
        assert format_day_time(datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)) == (
            "vendredi 28/08 14:00"
        )

    def test_heure_d_hiver_utc_moins_1(self):
        # 29/01/2027 23:30 UTC -> 30/01/2027 00:30 Paris (samedi).
        assert format_day_time(datetime(2027, 1, 29, 23, 30, tzinfo=timezone.utc)) == (
            "samedi 30/01 00:30"
        )

    def test_datetime_naif_traite_comme_utc(self):
        # SQLite (tests) retourne des datetimes naïfs toujours en UTC.
        assert format_day_time(datetime(2026, 8, 28, 12, 0)) == "vendredi 28/08 14:00"


class TestClosureEmbed:
    def _outcome(self):
        return CloseOutcome(
            position_id=1,
            signal_id=2,
            symbol="BTCUSDT",
            strategy="momentum_v1",
            action="BUY",
            entry_price=Decimal("100"),
            stop_loss=Decimal("98"),
            take_profit=Decimal("104"),
            risk_reward=Decimal("2"),
            exit_reason="TP",
            exit_price=Decimal("104"),
            result_r=Decimal("2"),
            opened_at=datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc),
            closed_at=datetime(2026, 8, 30, 9, 30, tzinfo=timezone.utc),
        )

    def test_champs_dates_ouverture_et_cloture(self):
        embed = build_closure_embed(self._outcome())
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Ouvert le"] == "vendredi 28/08 14:00"
        assert champs["Clôturé le"] == "dimanche 30/08 11:30"
        assert "heure de Paris" in embed.footer.text

    def test_champs_metier_inchanges(self):
        embed = build_closure_embed(self._outcome())
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Position"] == "LONG 🟢"
        assert champs["Sortie"] == "104"
        assert champs["Résultat"] == "+2R"  # format_r_fr : virgule FR, pas d'espace

    def test_cloture_be_titre_jaune_et_0r(self):
        """Clôture break-even : titre dédié, couleur jaune, résultat +0R."""
        outcome = replace(
            self._outcome(),
            exit_reason="BE",
            exit_price=Decimal("100"),
            result_r=Decimal("0"),
        )
        embed = build_closure_embed(outcome)
        assert embed.title == "🛡️ Break-even touché — BTCUSDT"
        assert embed.color.value == 0xF1C40F  # jaune : ni gain ni perte
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Sortie"] == "100"  # sortie à l'entrée
        assert champs["Résultat"] == "+0R"
        # Parcours de gestion rappelé : TP1 touché avant l'armement BE.
        assert "TP1" in champs["Gestion"]
        assert "BE armé" in champs["Gestion"]


class TestStatsEmbed:
    def _embed(self, **overrides):
        base = dict(
            total=3,
            par_statut={"SENT": 3},
            par_action={"BUY": 2, "SELL": 1},
            par_strategie={"momentum_v1": 3},
            paper=compute_stats([Decimal("2"), Decimal("2"), Decimal("-1")]),
            ouvertes=1,
        )
        base.update(overrides)
        return build_stats_embed(**base)

    def test_aucune_cloture_sans_ventilations(self):
        embed = self._embed(paper=compute_stats([]), ouvertes=2)
        champs = {f.name: f.value for f in embed.fields}
        assert "Aucune position clôturée (2 ouverte(s))" in champs["Paper trading (R)"]
        assert "Par symbole (R)" not in champs
        assert "Par direction (R)" not in champs

    def test_champs_enrichis(self):
        embed = self._embed()
        champs = {f.name: f.value for f in embed.fields}
        papier = champs["Paper trading (R)"]
        assert "Médiane : 2.00 R" in papier
        assert "Profit factor : 4.00" in papier
        assert "Meilleur : 2.00 R · Pire : -1.00 R" in papier

    def test_ventilations_et_equite(self):
        embed = self._embed(
            par_symbole={"BTCUSDT": compute_stats([Decimal("2")])},
            par_direction={"BUY": compute_stats([Decimal("2")])},
            sparkline="▁▄█",
        )
        champs = {f.name: f.value for f in embed.fields}
        assert "BTCUSDT : 1 trades · 100.00% · 2.00 R" in champs["Par symbole (R)"]
        assert "🟢 BUY : 1 trades · 100.00% · 2.00 R" in champs["Par direction (R)"]
        assert "Équité : ▁▄█" in champs["Paper trading (R)"]

    def test_profit_factor_none_affiche_tiret(self):
        # Aucune perte -> PF indéfini, affiché "—" (pas de crash).
        embed = self._embed(paper=compute_stats([Decimal("2")]))
        champs = {f.name: f.value for f in embed.fields}
        assert "Profit factor : —" in champs["Paper trading (R)"]

    def test_compte_trois_categories_avec_be(self):
        """🟢 gagnants · 🔴 perdants · ⚪ BE : un 0R n'est pas une perte."""
        embed = self._embed(
            paper=compute_stats([Decimal("2"), Decimal("0"), Decimal("-1")])
        )
        champs = {f.name: f.value for f in embed.fields}
        assert "(🟢 1W / 🔴 1L / ⚪ 1BE)" in champs["Paper trading (R)"]


class TestNumeroDeTrade:
    def test_champ_trade_affiche(self):
        embed = build_signal_embed(
            action="BUY",
            symbol="BTCUSDT",
            strategy="momentum_v1",
            timeframe="15",
            entry_price="100",
            stop_loss="98",
            take_profit="104",
            risk_reward="2",
            signal_time=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
            trade_number=16,
        )
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Trade"] == "#16"

    def test_sans_numero_pas_de_champ(self):
        embed = _embed()
        assert "Trade" not in {f.name for f in embed.fields}

    def test_champ_trade_embed_de_cloture(self):
        outcome = CloseOutcome(
            position_id=1,
            signal_id=2,
            symbol="BTCUSDT",
            strategy="momentum_v1",
            action="BUY",
            entry_price=Decimal("100"),
            stop_loss=Decimal("98"),
            take_profit=Decimal("104"),
            risk_reward=Decimal("2"),
            exit_reason="TP",
            exit_price=Decimal("104"),
            result_r=Decimal("2"),
            opened_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            closed_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
            sequence_number=16,
        )
        champs = {f.name: f.value for f in build_closure_embed(outcome).fields}
        assert champs["Trade"] == "#16"


class TestBeAlertEmbed:
    def _alert(self, sequence_number=16):
        from app.paper_trading.engine import BeAlert

        return BeAlert(
            position_id=7,
            sequence_number=sequence_number,
            symbol="BTCUSDT",
            action="BUY",
            entry_price=Decimal("100"),
            be_trigger=Decimal("103"),
        )

    def test_titre_et_champs(self):
        from app.discord.embeds import build_be_alert_embed

        embed = build_be_alert_embed(self._alert())
        assert embed.title == "🛡️ Break-even atteint — Trade #16"
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Symbole"] == "BTCUSDT"
        assert champs["Position"] == "LONG 🟢"
        assert champs["Déclencheur (+1,5R)"] == "103"
        assert champs["Action suggérée"] == "SL → entrée (100)"
        assert "aucun ordre" in embed.footer.text

    def test_sans_numero_symbole_en_titre(self):
        from app.discord.embeds import build_be_alert_embed

        embed = build_be_alert_embed(self._alert(sequence_number=None))
        assert embed.title == "🛡️ Break-even atteint — BTCUSDT"


class TestTpProgressEmbed:
    def _alert(self, level=1, niveaux=None):
        from app.paper_trading.engine import TpAlert

        return TpAlert(
            position_id=7,
            sequence_number=12,
            symbol="BTCUSDT",
            action="BUY",
            entry_price=Decimal("100"),
            level=level,
            level_price=Decimal("102"),
            niveaux=niveaux
            or ((1, Decimal("102"), True), (2, Decimal("104"), False)),
        )

    def test_titre_et_etat_des_tp(self):
        from app.discord.embeds import build_tp_progress_embed

        embed = build_tp_progress_embed(self._alert())
        assert embed.title == "✅ TP1 validé — Trade #12"
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Symbole"] == "BTCUSDT"
        assert champs["Position"] == "LONG 🟢"
        # Distance en pips depuis l'entrée (1 pip = 1 $) sur chaque niveau.
        assert champs["Take Profits"] == (
            "TP1 : ✅ validé (102 · +2 pips)\nTP2 : ⏳ en cours (104 · +4 pips)"
        )
        assert "aucun ordre" in embed.footer.text

    def test_tp2_avec_les_deux_valides(self):
        from app.discord.embeds import build_tp_progress_embed

        alert = self._alert(
            level=2,
            niveaux=((1, Decimal("102"), True), (2, Decimal("104"), True)),
        )
        embed = build_tp_progress_embed(alert)
        assert embed.title == "✅ TP2 validé — Trade #12"
        champs = {f.name: f.value for f in embed.fields}
        assert "TP1 : ✅ validé (102 · +2 pips)" in champs["Take Profits"]
        assert "TP2 : ✅ validé (104 · +4 pips)" in champs["Take Profits"]

    def test_sans_numero_symbole_en_titre(self):
        from app.discord.embeds import build_tp_progress_embed

        alert = self._alert()
        object.__setattr__(alert, "sequence_number", None)
        embed = build_tp_progress_embed(alert)
        assert embed.title == "✅ TP1 validé — BTCUSDT"


class TestScoreEtSetup:
    """Score recalibré sur 100 (max des composantes envoyées) + champ Setup."""

    def _embed(self, score, components):
        return build_signal_embed(
            action="BUY",
            symbol="BTCUSDT",
            strategy="momentum_v1",
            timeframe="15",
            entry_price="100",
            stop_loss="98",
            take_profit="104",
            risk_reward="2",
            signal_time=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
            score=score,
            score_components=components,
            trade_number=12,
        )

    def test_score_recalibre_sur_max_des_composantes(self):
        # momentum_v1 : max 55 points de barème. 45/55 -> 82/100.
        embed = self._embed(
            45, {"score_trend": 20, "score_momentum": 10, "score_macd": 15}
        )
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Signal Score"] == "82/100"
        assert "probabilité" in (embed.footer.text or "")

    def test_score_maximal_a_100(self):
        embed = self._embed(
            55, {"score_trend": 20, "score_momentum": 20, "score_macd": 15}
        )
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Signal Score"] == "100/100"

    def test_setup_avec_points_par_indicateur(self):
        embed = self._embed(
            55, {"score_trend": 20, "score_momentum": 20, "score_macd": 15}
        )
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Setup"] == (
            "Tendance — EMA 50/200 : 20/20\n"
            "Momentum — RSI 14 : 20/20\n"
            "MACD 12/26/9 : 15/15"
        )

    def test_sans_composantes_score_brut_et_setup_indicateurs(self):
        # Anciens signaux : pas de composantes -> score brut en points,
        # Setup limité aux indicateurs (sans points).
        embed = self._embed(45, None)
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Signal Score"] == "45 pts"
        assert champs["Setup"] == (
            "Tendance — EMA 50/200\nMomentum — RSI 14\nMACD 12/26/9"
        )

    def test_barème_complet_atteint_100_sans_recalage(self):
        # Toutes les composantes (max 100) : recalibrage neutre.
        embed = self._embed(
            75,
            {
                "score_trend": 20,
                "score_momentum": 20,
                "score_macd": 15,
                "score_volume": 5,
                "score_structure": 10,
                "score_htf": 5,
            },
        )
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Signal Score"] == "75/100"


class TestFormatPips:
    def test_distance_entiere(self):
        from app.discord.embeds import format_pips

        assert format_pips("BTCUSDT", "100000", "102500") == "+2,500 pips"

    def test_arrondi_au_plus_proche(self):
        from app.discord.embeds import format_pips

        assert format_pips("ETHUSDT", "3000", "3010.6") == "+11 pips"

    def test_distance_symetrique(self):
        from app.discord.embeds import format_pips

        assert format_pips("BTCUSDT", "102500", "100000") == "+2,500 pips"


class TestPerformanceEmbed:
    def test_periodes_et_detail(self):
        stats = compute_stats(
            [Decimal("2"), Decimal("2"), Decimal("-1"), Decimal("-1")]
        )
        embed = build_performance_embed(
            periodes=[
                ("7 jours", Decimal("1")),
                ("30 jours", Decimal("1")),
                ("90 jours", Decimal("2")),
                ("Total", Decimal("2")),
            ],
            stats=stats,
        )
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Résultat (R)"] == (
            "7 jours : +1R\n30 jours : +1R\n90 jours : +2R\nTotal : +2R"
        )
        detail = champs["Détail (historique complet)"]
        assert "Winrate : 50 %" in detail
        assert "Profit Factor : 2" in detail
        assert "Expectancy : +0,5R" in detail
        assert "Max Drawdown : -2R" in detail
        assert "Trades : 4" in detail
        assert "aucun ordre réel" in embed.footer.text

    def test_trades_repartition_trois_categories_avec_be(self):
        """Un 0R est une 3e catégorie ⚪ BE, pas un perdant."""
        stats = compute_stats([Decimal("2"), Decimal("0"), Decimal("-1")])
        embed = build_performance_embed(
            periodes=[("Total", Decimal("1"))],
            stats=stats,
        )
        champs = {f.name: f.value for f in embed.fields}
        assert "Trades : 3 (🟢 1 · 🔴 1 · ⚪ 1 BE)" in champs["Détail (historique complet)"]
