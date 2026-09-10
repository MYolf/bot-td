"""Tests des embeds Discord (Phase 12)."""

from datetime import datetime, timezone
from decimal import Decimal

from app.discord.embeds import (
    GREEN,
    RED,
    build_closure_embed,
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
        assert champs["Take Profit"] == "106,000"
        assert champs["Risk/Reward"] == "1:2"
        assert champs["Signal Time"] == "22:14:03 UTC"

    def test_sorties_partielles_buy(self):
        # Risque = 104532.42 - 103800 = 732.42 -> TP1/2/3 à +1R/+2R/+3R,
        # déclencheur BE à +1,5R (mi-chemin TP1->TP2).
        embed = _embed("BUY")
        champs = {f.name: f.value for f in embed.fields}
        partielles = champs["Sorties partielles (suggestion)"]
        assert partielles == (
            "TP1 : 105,264.84 (+1R)\nTP2 : 105,997.26 (+2R)\nTP3 : 106,729.68 (+3R)\n"
            "BE : SL → entrée à 105,631.05 (+1,5R)"
        )

    def test_sorties_partielles_sell(self):
        # SELL : risque = SL - entry = 103800 - 104532.42 = 732.42 vers le bas.
        embed = _embed("SELL")
        champs = {f.name: f.value for f in embed.fields}
        partielles = champs["Sorties partielles (suggestion)"]
        assert partielles == (
            "TP1 : 103,800 (+1R)\nTP2 : 103,067.58 (+2R)\nTP3 : 102,335.16 (+3R)\n"
            "BE : SL → entrée à 103,433.79 (+1,5R)"
        )


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
        assert champs["Résultat"] == "+2 R"


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
