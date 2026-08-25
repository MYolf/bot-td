"""Tests du webhook TradingView (Phases 4, 5, 7, 27).

Catalogue obligatoire Projet.md §41 couvert ici : webhook valide/invalide,
secret invalide ou absent, JSON invalide. Les rejets métiers en 200 sont
testés en bout en bout (timestamp expiré, incohérences SL/TP) : rien en
base, aucune notification.
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.test_notification import _signals


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_payload() -> dict:
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
        "timestamp": _now_iso(),
    }


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_valid_buy(client):
    response = client.post("/webhook/tradingview", json=_valid_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "sent"
    assert body["signal_id"] == 1


def test_webhook_valid_sell(client):
    payload = _valid_payload()
    payload["action"] = "SELL"
    payload["stop_loss"] = "105200.00"
    payload["take_profit"] = "103200.00"
    response = client.post("/webhook/tradingview", json=payload)
    assert response.status_code == 200


def test_webhook_invalid_secret(client):
    payload = _valid_payload()
    payload["secret"] = "mauvais-secret"
    response = client.post("/webhook/tradingview", json=payload)
    assert response.status_code == 401
    # Le secret ne doit jamais apparaître dans la réponse.
    assert "mauvais-secret" not in response.text


@pytest.mark.parametrize("secret", [None, ""], ids=["absent", "vide"])
def test_webhook_missing_secret(client, secret):
    """Le secret est obligatoire : absent ou vide => 422, jamais traité."""
    payload = _valid_payload()
    if secret is None:
        del payload["secret"]
    else:
        payload["secret"] = secret
    response = client.post("/webhook/tradingview", json=payload)
    assert response.status_code == 422


def test_webhook_invalid_json(client):
    response = client.post(
        "/webhook/tradingview",
        content="{ce n'est pas du json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_webhook_missing_fields(client):
    payload = _valid_payload()
    del payload["stop_loss"]
    response = client.post("/webhook/tradingview", json=payload)
    assert response.status_code == 422


def test_webhook_invalid_action(client):
    payload = _valid_payload()
    payload["action"] = "HOLD"
    response = client.post("/webhook/tradingview", json=payload)
    assert response.status_code == 422


def test_webhook_negative_price(client):
    payload = _valid_payload()
    payload["price"] = "-10"
    response = client.post("/webhook/tradingview", json=payload)
    assert response.status_code == 422


def test_webhook_timeframe_normalized(client):
    """TradingView peut envoyer "1H" : normalisé en "60" puis validé."""
    payload = _valid_payload()
    payload["timeframe"] = "1H"
    response = client.post("/webhook/tradingview", json=payload)
    assert response.status_code == 200


# --- Phase 27 : rejets métiers en bout en bout (catalogue §41) ---


class TestRejetsMetiersE2E:
    """Un rejet métier : 200 + raison, rien en base, aucune notification."""

    def test_timestamp_expire_rejete_e2e(self, client):
        payload = _valid_payload()
        payload["timestamp"] = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat()
        response = client.post("/webhook/tradingview", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert response.json()["reason"] == "expired_timestamp"
        assert _signals(client) == []
        assert len(client.notifier.sent) == 0

    @pytest.mark.parametrize(
        ("action", "stop_loss", "take_profit", "reason"),
        [
            ("BUY", "105000.00", "106000.00", "incoherent_stop_loss"),
            ("BUY", "103800.00", "104000.00", "incoherent_take_profit"),
            ("SELL", "104000.00", "103200.00", "incoherent_stop_loss"),
            ("SELL", "105200.00", "105000.00", "incoherent_take_profit"),
        ],
        ids=["buy_sl_incoherent", "buy_tp_incoherent", "sell_sl_incoherent", "sell_tp_incoherent"],
    )
    def test_prix_incoherents_rejetes_e2e(
        self, client, action, stop_loss, take_profit, reason
    ):
        payload = _valid_payload()
        payload.update(
            action=action, stop_loss=stop_loss, take_profit=take_profit
        )
        response = client.post("/webhook/tradingview", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert response.json()["reason"] == reason
        assert _signals(client) == []
        assert len(client.notifier.sent) == 0
