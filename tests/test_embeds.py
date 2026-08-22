"""Tests des embeds Discord (Phase 12)."""

from datetime import datetime, timezone
from decimal import Decimal

from app.discord.embeds import (
    GREEN,
    RED,
    build_signal_embed,
    format_price,
    format_risk_reward,
    strategy_label,
    timeframe_label,
)


def _embed(action: str = "BUY"):
    return build_signal_embed(
        action=action,
        symbol="BTCUSDT",
        strategy="momentum_v1",
        timeframe="15",
        entry_price="104532.42",
        stop_loss="103800",
        take_profit="106000",
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
