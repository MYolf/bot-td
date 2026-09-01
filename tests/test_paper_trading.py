"""Tests du paper trading (Phase 21).

Catalogue imposé (Projet.md §41 / skill testing) : paper trade BUY, paper
trade SELL, TP atteint (+RR), SL atteint (-1R), calcul du résultat en R,
statistiques (win rate, total R). Simulation locale : aucun réseau.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.database.models import PaperPosition, PaperTrade
from app.database.repository import PaperRepository
from app.paper_trading.engine import resolve_exit, result_in_r
from app.paper_trading.statistics import compute_stats, equity_sparkline, paper_breakdown


def _payload(
    *,
    action: str = "BUY",
    price: str = "100",
    stop_loss: str = "98",
    take_profit: str = "104",
    timestamp: datetime | None = None,
    symbol: str = "BTCUSDT",
) -> dict:
    """BUY cohérent par défaut : SL < entrée < TP, RR = 2."""
    return {
        "secret": "secret-test",
        "strategy": "momentum_v1",
        "symbol": symbol,
        "exchange": "BINANCE",
        "timeframe": "15",
        "action": action,
        "price": price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
    }


def _later(seconds: int = 30) -> datetime:
    """Timestamp distinct mais toujours frais (tolérance futur : 60 s)."""
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _positions(client) -> list[PaperPosition]:
    async def load():
        async with client.db_factory() as session:
            return list(
                (await session.execute(select(PaperPosition))).scalars().all()
            )

    return asyncio.run(load())


def _trades(client) -> list[PaperTrade]:
    async def load():
        async with client.db_factory() as session:
            return list((await session.execute(select(PaperTrade))).scalars().all())

    return asyncio.run(load())


def _fermees(client) -> list[PaperPosition]:
    return [p for p in _positions(client) if p.status == "CLOSED"]


def _ouvertes(client) -> list[PaperPosition]:
    return [p for p in _positions(client) if p.status == "OPEN"]


def _closed_rows(client, **filters) -> list[tuple]:
    async def load():
        async with client.db_factory() as session:
            return await PaperRepository(session).closed_rows(**filters)

    return asyncio.run(load())


# --- Logique pure ---

class TestResolveExit:
    def test_buy_sl_atteint(self):
        assert resolve_exit("BUY", Decimal("97.5"), Decimal("98"), Decimal("104")) == "SL"

    def test_buy_tp_atteint(self):
        assert resolve_exit("BUY", Decimal("104"), Decimal("98"), Decimal("104")) == "TP"

    def test_buy_entre_les_deux(self):
        assert resolve_exit("BUY", Decimal("100"), Decimal("98"), Decimal("104")) is None

    def test_sell_sl_atteint(self):
        assert resolve_exit("SELL", Decimal("102.5"), Decimal("102"), Decimal("96")) == "SL"

    def test_sell_tp_atteint(self):
        assert resolve_exit("SELL", Decimal("95.9"), Decimal("102"), Decimal("96")) == "TP"

    def test_sell_entre_les_deux(self):
        assert resolve_exit("SELL", Decimal("100"), Decimal("102"), Decimal("96")) is None


class TestResultInR:
    def test_tp_vaut_le_rr(self):
        assert result_in_r("TP", Decimal("2.0000")) == Decimal("2")

    def test_sl_vaut_moins_1r(self):
        assert result_in_r("SL", Decimal("2.0000")) == Decimal("-1")


class TestComputeStats:
    def test_aucun_trade(self):
        stats = compute_stats([])
        assert stats.total == 0
        assert stats.profit_factor is None
        assert stats.total_r == 0

    def test_exemple_projet(self):
        # Exemple Projet.md §33 : entry 100, SL 98, TP 104 -> RR 2.
        # 2 TP (+2R) et 2 SL (-1R).
        stats = compute_stats([Decimal("2"), Decimal("2"), Decimal("-1"), Decimal("-1")])
        assert stats.total == 4
        assert stats.wins == 2
        assert stats.losses == 2
        assert stats.win_rate == Decimal("50")
        assert stats.total_r == Decimal("2")
        assert stats.avg_r == Decimal("0.5")  # expectancy > 0
        assert stats.best_r == Decimal("2")
        assert stats.worst_r == Decimal("-1")
        assert stats.profit_factor == Decimal("2")  # 4 / 2
        # Cumul 2, 4, 3, 2 : peak 4 puis retour à 2 -> drawdown max 2.
        assert stats.max_drawdown_r == Decimal("2")

    def test_max_drawdown_cumule(self):
        # Séquence : +1, -1, -1, +1 -> drawdown max 2 (après le peak à 1).
        stats = compute_stats([Decimal("1"), Decimal("-1"), Decimal("-1"), Decimal("1")])
        assert stats.max_drawdown_r == Decimal("2")
        assert stats.total_r == 0

    def test_mediane_impair(self):
        # Tri : -1, 2, 3 -> valeur centrale 2.
        stats = compute_stats([Decimal("2"), Decimal("-1"), Decimal("3")])
        assert stats.median_r == Decimal("2")

    def test_mediane_pair(self):
        # Tri : -1, -1, 2, 2 -> moyenne des deux centraux = 0.5.
        stats = compute_stats([Decimal("2"), Decimal("2"), Decimal("-1"), Decimal("-1")])
        assert stats.median_r == Decimal("0.5")


class TestPaperBreakdown:
    def test_ventilation_symbole_et_direction(self):
        rows = [
            (Decimal("2"), "BTCUSDT", "BUY"),
            (Decimal("-1"), "ETHUSDT", "SELL"),
            (Decimal("2"), "ETHUSDT", "BUY"),
        ]
        par_symbole, par_direction = paper_breakdown(rows)
        assert list(par_symbole) == ["BTCUSDT", "ETHUSDT"]  # triées
        assert par_symbole["BTCUSDT"].total == 1
        assert par_symbole["BTCUSDT"].total_r == Decimal("2")
        assert par_symbole["ETHUSDT"].total == 2
        assert par_symbole["ETHUSDT"].total_r == Decimal("1")
        assert par_direction["BUY"].total == 2
        assert par_direction["BUY"].total_r == Decimal("4")
        assert par_direction["SELL"].total == 1
        assert par_direction["SELL"].total_r == Decimal("-1")

    def test_vide(self):
        par_symbole, par_direction = paper_breakdown([])
        assert par_symbole == {}
        assert par_direction == {}


class TestEquitySparkline:
    def test_moins_de_deux_trades_vide(self):
        assert equity_sparkline([]) == ""
        assert equity_sparkline([Decimal("2")]) == ""

    def test_croissante(self):
        # Cumul 1, 2, 3 -> min au début, max à la fin.
        assert equity_sparkline([Decimal("1")] * 3, width=3) == "▁▄█"

    def test_decroissante(self):
        # Cumul 1, 0, -1 -> peak au début, creux à la fin.
        assert equity_sparkline([Decimal("1"), Decimal("-1"), Decimal("-1")], width=3) == "█▄▁"


# --- Intégration webhook -> moteur -> base ---

class TestPaperTradingViaWebhook:
    def test_signal_buy_ouvre_position_ouverte(self, client):
        response = client.post("/webhook/tradingview", json=_payload())
        assert response.status_code == 200
        (position,) = _positions(client)
        assert position.status == "OPEN"
        assert position.result_r is None
        assert position.closed_at is None

    def test_signal_sell_ouvre_position(self, client):
        payload = _payload(action="SELL", stop_loss="102", take_profit="96")
        response = client.post("/webhook/tradingview", json=payload)
        assert response.status_code == 200
        (position,) = _positions(client)
        assert position.status == "OPEN"

    def test_tp_atteint_classe_en_rr(self, client):
        # Position BUY : entry 100, SL 98, TP 104 (RR = 2).
        client.post("/webhook/tradingview", json=_payload())
        # Signal ultérieur sur le même symbole dont le prix dépasse le TP.
        suivant = _payload(price="105", stop_loss="103", take_profit="108", timestamp=_later())
        client.post("/webhook/tradingview", json=suivant)

        # Chaque signal stocké ouvre sa position : la première est clôturée,
        # la deuxième reste ouverte.
        (fermee,) = _fermees(client)
        assert fermee.result_r == Decimal("2")  # +RR
        assert fermee.closed_at is not None
        assert len(_ouvertes(client)) == 1
        (trade,) = _trades(client)
        assert trade.exit_reason == "TP"
        assert trade.exit_price == Decimal("105")

    def test_sl_atteint_classe_en_moins_1r(self, client):
        client.post("/webhook/tradingview", json=_payload())
        suivant = _payload(price="97.5", stop_loss="95", take_profit="101", timestamp=_later())
        client.post("/webhook/tradingview", json=suivant)

        (fermee,) = _fermees(client)
        assert fermee.result_r == Decimal("-1")  # -1R
        (trade,) = _trades(client)
        assert trade.exit_reason == "SL"
        assert trade.exit_price == Decimal("97.5")

    def test_sell_tp_atteint(self, client):
        client.post(
            "/webhook/tradingview",
            json=_payload(action="SELL", stop_loss="102", take_profit="96"),
        )
        suivant = _payload(
            action="SELL", price="95.9", stop_loss="98", take_profit="92", timestamp=_later()
        )
        client.post("/webhook/tradingview", json=suivant)

        (fermee,) = _fermees(client)
        assert fermee.result_r == Decimal("2")  # SELL : RR = (100-96)/(102-100) = 2

    def test_prix_neutre_ne_cloture_pas(self, client):
        client.post("/webhook/tradingview", json=_payload())
        suivant = _payload(price="101", stop_loss="99", take_profit="104", timestamp=_later())
        client.post("/webhook/tradingview", json=suivant)

        assert _fermees(client) == []
        assert len(_ouvertes(client)) == 2
        assert _trades(client) == []

    def test_doublon_une_seule_position(self, client):
        payload = _payload()
        client.post("/webhook/tradingview", json=payload)
        client.post("/webhook/tradingview", json=payload)  # doublon
        assert len(_positions(client)) == 1

    def test_symboles_independants(self, client):
        client.post("/webhook/tradingview", json=_payload(symbol="BTCUSDT"))
        # Prix d'ETH très bas : ne doit pas clôturer la position BTC.
        client.post(
            "/webhook/tradingview",
            json=_payload(symbol="ETHUSDT", price="50", stop_loss="49", take_profit="52", timestamp=_later()),
        )
        assert _fermees(client) == []
        assert len(_ouvertes(client)) == 2


class TestClosedRows:
    def test_rows_et_filtres(self, client):
        # BTC : BUY clôturé TP (+2R) ; ETH : SELL clôturé SL (-1R).
        client.post("/webhook/tradingview", json=_payload(symbol="BTCUSDT"))
        client.post(
            "/webhook/tradingview",
            json=_payload(
                symbol="BTCUSDT", price="105", stop_loss="103", take_profit="108",
                timestamp=_later(),
            ),
        )
        client.post(
            "/webhook/tradingview",
            json=_payload(action="SELL", symbol="ETHUSDT", stop_loss="102", take_profit="96"),
        )
        client.post(
            "/webhook/tradingview",
            json=_payload(
                action="SELL", symbol="ETHUSDT", price="103", stop_loss="105",
                take_profit="99", timestamp=_later(),
            ),
        )

        rows = _closed_rows(client)
        assert sorted(rows) == [
            (Decimal("-1"), "ETHUSDT", "SELL"),
            (Decimal("2"), "BTCUSDT", "BUY"),
        ]
        # Ventilation : un groupe par symbole et par direction.
        par_symbole, par_direction = paper_breakdown(rows)
        assert set(par_symbole) == {"BTCUSDT", "ETHUSDT"}
        assert set(par_direction) == {"BUY", "SELL"}

        assert _closed_rows(client, timeframe="60") == []  # tout est en 15m
        assert len(_closed_rows(client, timeframe="15")) == 2
        assert len(_closed_rows(client, strategy="momentum_v1")) == 2


class TestFiabilite:
    def test_echec_paper_trading_ne_casse_pas_le_webhook(self, client, caplog):
        """Le paper trading est best-effort : un échec ne doit jamais
        compromettre le signal (statut SENT, notification envoyuee)."""

        class BrokenEngine:
            async def process_signal(self, **kwargs):
                raise RuntimeError("paper trading cassé (test)")

        from app.paper_trading.engine import provide_paper_engine

        client.app.dependency_overrides[provide_paper_engine] = lambda: BrokenEngine()
        try:
            with caplog.at_level(logging.ERROR):
                response = client.post("/webhook/tradingview", json=_payload())
        finally:
            client.app.dependency_overrides.pop(provide_paper_engine, None)

        assert response.status_code == 200
        assert response.json()["status"] == "sent"
        assert len(client.notifier.sent) == 1
        assert any("Paper trading échoué" in r.message for r in caplog.records)
        assert _positions(client) == []
