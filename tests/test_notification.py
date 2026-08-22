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

        assert response.status_code == 202
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
        assert response.status_code == 202
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

        assert response.status_code == 400
        assert len(client.notifier.sent) == 0
        assert _signals(client) == []
