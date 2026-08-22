"""Tests du webhook TradingView (Phases 4, 5, 7)."""

from datetime import datetime, timedelta, timezone


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
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["signal_id"] == 1


def test_webhook_valid_sell(client):
    payload = _valid_payload()
    payload["action"] = "SELL"
    payload["stop_loss"] = "105200.00"
    payload["take_profit"] = "103200.00"
    response = client.post("/webhook/tradingview", json=payload)
    assert response.status_code == 202


def test_webhook_invalid_secret(client):
    payload = _valid_payload()
    payload["secret"] = "mauvais-secret"
    response = client.post("/webhook/tradingview", json=payload)
    assert response.status_code == 401
    # Le secret ne doit jamais apparaître dans la réponse.
    assert "mauvais-secret" not in response.text


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
    assert response.status_code == 202
