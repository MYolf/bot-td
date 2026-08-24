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
from app.paper_trading.engine import PaperTradingEngine, provide_paper_engine  # noqa: E402
from app.services.discord_service import provide_notifier  # noqa: E402


class FakeNotifier:
    """Notifieur factice : enregistre les embeds envoyés, sans Discord."""

    def __init__(self):
        self.sent: list = []
        self.fail = False

    async def send_signal(self, embed) -> int:
        if self.fail:
            from app.services.discord_service import DiscordSendError

            raise DiscordSendError("discord indisponible (test)")
        self.sent.append(embed)
        return 1000 + len(self.sent)  # message_id factice


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
    fake_notifier = FakeNotifier()
    paper_engine = PaperTradingEngine(factory)

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[provide_session_factory] = lambda: factory
    app.dependency_overrides[provide_notifier] = lambda: fake_notifier
    app.dependency_overrides[provide_paper_engine] = lambda: paper_engine
    with TestClient(app) as test_client:
        test_client.notifier = fake_notifier  # type: ignore[attr-defined]
        test_client.db_factory = factory  # type: ignore[attr-defined]
        test_client.paper_engine = paper_engine  # type: ignore[attr-defined]
        yield test_client
    app.dependency_overrides.clear()
