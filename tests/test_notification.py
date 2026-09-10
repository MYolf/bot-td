"""Tests du pipeline complet : stockage puis notification Discord (Phases 12-13)."""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.database.models import Signal


def _payload() -> dict:
    return {
        "secret": "secret-test",
        "strategy": "momentum_v1",
        "symbol": "BTCUSDT",
        "exchange": "BINANCE",
        "timeframe": "15",
        "action": "BUY",
        "price": "104532.42",
        "stop_loss": "103800.00",
        "take_profit": "106000.00",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _signals(client) -> list[Signal]:
    """Charge les signaux via la factory SQLite du fixture client."""

    async def load():
        async with client.db_factory() as session:
            return list((await session.execute(select(Signal))).scalars().all())

    return asyncio.run(load())


class TestPipelineNotification:
    def test_signal_valide_stocke_puis_notifie(self, client):
        response = client.post("/webhook/tradingview", json=_payload())

        assert response.status_code == 200
        assert response.json()["status"] == "sent"
        # Un seul embed envoyé
        assert len(client.notifier.sent) == 1
        embed = client.notifier.sent[0]
        assert embed.title == "🟢 LONG SIGNAL — BTCUSDT"
        # Statut SENT + message_id stockés (traçabilité)
        (signal,) = _signals(client)
        assert signal.status == "SENT"
        assert signal.discord_message_id is not None

    def test_echec_discord_signal_conserve_en_error(self, client):
        client.notifier.fail = True
        response = client.post("/webhook/tradingview", json=_payload())

        # Le signal est stocké mais pas notifié : il reste en base (ERROR).
        assert response.status_code == 200
        assert response.json()["status"] == "stored_not_notified"
        assert len(client.notifier.sent) == 0
        (signal,) = _signals(client)
        assert signal.status == "ERROR"

    def test_doublon_jamais_deux_notifications(self, client):
        payload = _payload()
        premier = client.post("/webhook/tradingview", json=payload)
        second = client.post("/webhook/tradingview", json=payload)

        assert premier.json()["status"] == "sent"
        assert second.json()["status"] == "duplicate"
        # Invariant absolu : un doublon ne produit jamais deux notifications.
        assert len(client.notifier.sent) == 1
        # Une seule ligne en base, statut SENT
        signals = _signals(client)
        assert len(signals) == 1
        assert signals[0].status == "SENT"

    def test_signal_rejete_aucune_notification(self, client):
        payload = _payload()
        payload["symbol"] = "DOGEUSDT"  # hors liste blanche
        response = client.post("/webhook/tradingview", json=payload)

        # Rejet métier : 200 + statut explicite (TradingView attend un 2xx).
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert len(client.notifier.sent) == 0
        assert _signals(client) == []


class TestNumerotation:
    """Numéro de trade séquentiel : 1, 2, 3... en base et dans les embeds."""

    def test_numeros_sequentiels_en_base_et_embeds(self, client):
        from datetime import timedelta

        premier = client.post("/webhook/tradingview", json=_payload())
        payload_2 = _payload()
        payload_2["timestamp"] = (
            datetime.now(timezone.utc) + timedelta(seconds=30)
        ).isoformat()
        payload_2["action"] = "SELL"
        payload_2["stop_loss"] = "105200.00"
        payload_2["take_profit"] = "103200.00"
        second = client.post("/webhook/tradingview", json=payload_2)

        assert premier.json()["status"] == "sent"
        assert second.json()["status"] == "sent"
        numeros = [s.sequence_number for s in _signals(client)]
        assert sorted(numeros) == [1, 2]
        # Les embeds portent le numéro du trade.
        champs_1 = {f.name: f.value for f in client.notifier.sent[0].fields}
        champs_2 = {f.name: f.value for f in client.notifier.sent[1].fields}
        assert champs_1["Trade"] == "#1"
        assert champs_2["Trade"] == "#2"

    def test_doublon_ne_consomme_pas_de_numero(self, client):
        payload = _payload()
        client.post("/webhook/tradingview", json=payload)
        second = client.post("/webhook/tradingview", json=payload)

        assert second.json()["status"] == "duplicate"
        (signal,) = _signals(client)
        assert signal.sequence_number == 1
