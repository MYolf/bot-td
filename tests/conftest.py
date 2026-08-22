"""Fixtures partagées des tests.

Fournit un environnement minimal (variables d'environnement factices) AVANT
tout import de l'application, puis un client HTTP de test.
"""

import os

# Valeurs factices posées avant l'import de app.main (Settings est requis au démarrage).
os.environ.setdefault("DISCORD_BOT_TOKEN", "token-test")
os.environ.setdefault("TRADINGVIEW_WEBHOOK_SECRET", "secret-test")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb")
# Pas de connexion Discord réelle pendant les tests.
os.environ.setdefault("DISCORD_ENABLED", "false")

import asyncio  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config.settings import Settings, get_settings  # noqa: E402
from app.database.database import provide_session_factory  # noqa: E402
from app.database.models import Base  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def test_settings() -> Settings:
    """Paramètres isolés du fichier .env local (variables d'environnement uniquement)."""
    return Settings(_env_file=None)


@pytest.fixture
def client(test_settings: Settings) -> TestClient:
    """Client HTTP : configuration de test + SQLite async en mémoire pour la DB.

    Pas besoin de PostgreSQL pour les tests : la contrainte UNIQUE sur
    signal_uid s'applique aussi sur SQLite.
    """

    async def _create_tables() -> async_sessionmaker:
        engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,  # une seule connexion partagée en mémoire
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, expire_on_commit=False)

    factory = asyncio.run(_create_tables())

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[provide_session_factory] = lambda: factory
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
