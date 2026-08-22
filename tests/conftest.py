"""Fixtures partagées des tests.

Fournit un environnement minimal (variables d'environnement factices) AVANT
tout import de l'application, puis un client HTTP de test.
"""

import os

# Valeurs factices posées avant l'import de app.main (Settings est requis au démarrage).
os.environ.setdefault("DISCORD_BOT_TOKEN", "token-test")
os.environ.setdefault("TRADINGVIEW_WEBHOOK_SECRET", "secret-test")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config.settings import Settings, get_settings  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def test_settings() -> Settings:
    """Paramètres isolés du fichier .env local (variables d'environnement uniquement)."""
    return Settings(_env_file=None)


@pytest.fixture
def client(test_settings: Settings) -> TestClient:
    """Client HTTP avec la configuration de test injectée."""
    app.dependency_overrides[get_settings] = lambda: test_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
