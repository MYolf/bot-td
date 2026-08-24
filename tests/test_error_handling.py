"""Tests de la gestion des erreurs (Phase 18).

Cas imposés par Projet.md §28 : base de données indisponible, exception
inattendue, et la règle absolue : AUCUN secret (token Discord, secret
webhook, mot de passe DB) ne doit jamais apparaître dans les logs.
"""

import logging
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings, get_settings
from app.main import app
from app.database.database import provide_session_factory
from app.services.discord_service import provide_notifier
from tests.conftest import FakeNotifier


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
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class BrokenSessionFactory:
    """Simule une base de données totalement indisponible."""

    def __call__(self):
        raise ConnectionError("database unreachable (test)")


@pytest.fixture
def db_down_client(test_settings: Settings):
    """Client avec une fabrique de sessions qui échoue toujours."""
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[provide_session_factory] = lambda: BrokenSessionFactory()
    app.dependency_overrides[provide_notifier] = lambda: FakeNotifier()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


class TestDatabaseIndisponible:
    def test_db_indisponible_repond_500(self, db_down_client):
        response = db_down_client.post("/webhook/tradingview", json=_valid_payload())
        assert response.status_code == 500
        body = response.json()
        assert body["status"] == "error"
        assert body["error"] == "database_unavailable"
        # Jamais de détail d'exception côté client.
        assert "unreachable" not in response.text

    def test_db_indisponible_est_loggee(self, db_down_client, caplog):
        with caplog.at_level(logging.ERROR):
            db_down_client.post("/webhook/tradingview", json=_valid_payload())
        assert any("Erreur base de données" in r.message for r in caplog.records)


class TestExceptionNonGeree:
    def test_exception_inattendue_repond_500(self, client, monkeypatch):
        """Le handler global doit transformer toute exception non gérée en 500."""
        from app.api import tradingview

        def _crash(signal, settings):
            raise RuntimeError("bug inattendu (test)")

        monkeypatch.setattr(tradingview, "validate_signal", _crash)
        # Sans cette option, TestClient re-lèverait l'exception au lieu de
        # passer par le handler global.
        with TestClient(app, raise_server_exceptions=False) as raw_client:
            response = raw_client.post("/webhook/tradingview", json=_valid_payload())
        assert response.status_code == 500
        assert response.json() == {"status": "error", "error": "internal_error"}
        # Le détail de l'exception ne fuit jamais vers le client.
        assert "bug inattendu" not in response.text


class TestSecretsJamaisDansLesLogs:
    def test_aucun_secret_dans_les_logs(self, client, db_down_client, caplog):
        """Token Discord, secret webhook et mot de passe DB interdits dans les logs,
        y compris sur les chemins d'erreur (401, rejet, 500)."""
        with caplog.at_level(logging.INFO):
            # Secret invalide (401)
            mauvais = _valid_payload() | {"secret": "secret-incorrect"}
            client.post("/webhook/tradingview", json=mauvais)
            # Rejet métier (200 + rejected)
            rejete = _valid_payload() | {"symbol": "DOGEUSDT"}
            client.post("/webhook/tradingview", json=rejete)
            # Signal valide complet
            client.post("/webhook/tradingview", json=_valid_payload())
            # Défaillance DB (500)
            db_down_client.post("/webhook/tradingview", json=_valid_payload())

        secrets_interdits = [
            "secret-test",  # secret webhook
            "secret-incorrect",  # secret souis par l'appelant
            "token-test",  # token Discord (valeur de test posée par conftest)
            "motdepasse",  # mot de passe DB
        ]
        for secret in secrets_interdits:
            assert secret not in caplog.text, f"SECRET DANS LES LOGS : {secret}"
