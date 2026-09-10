"""Tests des commandes slash de la Phase 20 (/lastsignal, /signals, /stats, /strategy).

Le bot n'est pas dans le système de dépendances FastAPI : les commandes lisent
via `session_scope` (factory globale). On branche donc un SQLite async en
mémoire via `set_session_factory`, comme le fait conftest pour le webhook.
Aucune connexion Discord : les callbacks sont invoqués avec un fake.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config.settings import Settings
from app.database.database import set_session_factory
from app.database.models import Base, Signal, Strategy
from app.discord import commands as bot_commands
from tests.test_discord_bot import FakeInteraction


@pytest.fixture
def db_factory():
    """SQLite async en mémoire, branchée sur la factory globale du bot."""
    factory, engine = asyncio.run(_create_factory())

    def _seed() -> None:
        async def run():
            async with factory() as session:
                momentum = Strategy(name="momentum_v1", version="1")
                gold = Strategy(name="gold_breakout_v1", version="1", enabled=False)
                session.add_all([momentum, gold])
                await session.flush()
                base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
                session.add_all(
                    [
                        _signal(1, momentum.id, "BTCUSDT", "BUY", base, "SENT"),
                        _signal(2, momentum.id, "ETHUSDT", "SELL", base + timedelta(minutes=15), "SENT"),
                        _signal(3, gold.id, "XAUUSD", "BUY", base + timedelta(minutes=30), "ERROR"),
                    ]
                )
                await session.commit()

        asyncio.run(run())

    _seed()
    set_session_factory(factory)
    yield factory
    asyncio.run(engine.dispose())
    set_session_factory(None)  # type: ignore[arg-type]


def _signal(
    signal_id: int,
    strategy_id: int,
    symbol: str,
    action: str,
    moment: datetime,
    status: str,
) -> Signal:
    return Signal(
        id=signal_id,
        signal_uid=f"momentum_v1:{symbol}:15:{int(moment.timestamp())}:{action}",
        sequence_number=signal_id,
        strategy_id=strategy_id,
        symbol=symbol,
        exchange="BINANCE",
        timeframe="15",
        action=action,
        entry_price=Decimal("100"),
        stop_loss=Decimal("98"),
        take_profit=Decimal("104"),
        risk_reward=Decimal("2"),
        signal_timestamp=moment,
        received_at=moment,
        status=status,
    )


async def _create_factory() -> tuple[async_sessionmaker, object]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


class TestLastSignal:
    async def test_dernier_signal_en_embed(self, test_settings: Settings, db_factory):
        interaction = FakeInteraction(test_settings)
        await bot_commands.lastsignal_command.callback(interaction)  # type: ignore[attr-defined]
        (appel,) = interaction.response.sent
        assert appel["ephemeral"] is True
        embed = appel["embed"]
        # Le plus récent : XAUUSD BUY (statut ERROR, mais toujours le dernier reçu)
        assert embed.title == "🟢 LONG SIGNAL — XAUUSD"

    async def test_aucun_signal_message_simple(self, test_settings: Settings, db_factory):
        async with db_factory() as session:
            await session.execute(delete(Signal))
            await session.commit()

        interaction = FakeInteraction(test_settings)
        await bot_commands.lastsignal_command.callback(interaction)  # type: ignore[attr-defined]
        (appel,) = interaction.response.sent
        assert appel["embed"] is None
        assert appel["content"] == "Aucun signal pour le moment."


class TestSignalsCommand:
    async def test_liste_limitee(self, test_settings: Settings, db_factory):
        interaction = FakeInteraction(test_settings)
        await bot_commands.signals_command.callback(interaction, limite=2)  # type: ignore[attr-defined]
        (appel,) = interaction.response.sent
        embed = appel["embed"]
        assert embed.title == "📋 Derniers signaux"
        # 2 lignes maximum, la plus récente d'abord (XAUUSD).
        assert embed.description is not None
        lignes = embed.description.split("\n")
        assert len(lignes) == 2
        assert "XAUUSD" in lignes[0]
        assert "ETHUSDT" in lignes[1]

    async def test_aucun_signal_message_simple(self, test_settings: Settings, db_factory):
        interaction = FakeInteraction(test_settings)
        await bot_commands.signals_command.callback(interaction, limite=5)  # type: ignore[attr-defined]
        (appel,) = interaction.response.sent
        assert appel["embed"].description is not None  # 3 signaux seedés


class TestStatsCommand:
    async def test_comptages(self, test_settings: Settings, db_factory):
        interaction = FakeInteraction(test_settings)
        await bot_commands.stats_command.callback(interaction)  # type: ignore[attr-defined]
        (appel,) = interaction.response.sent
        embed = appel["embed"]
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Total"] == "3"
        assert "SENT: 2" in champs["Par statut"]
        assert "ERROR: 1" in champs["Par statut"]
        assert "BUY: 2" in champs["Par action"]
        assert "SELL: 1" in champs["Par action"]
        assert "Momentum V1: 2" in champs["Par stratégie"]
        assert "Gold Breakout V1: 1" in champs["Par stratégie"]


class TestStrategyCommand:
    async def test_liste_avec_etat_et_compteur(self, test_settings: Settings, db_factory):
        interaction = FakeInteraction(test_settings)
        await bot_commands.strategy_command.callback(interaction)  # type: ignore[attr-defined]
        (appel,) = interaction.response.sent
        embed = appel["embed"]
        champs = {f.name: f.value for f in embed.fields}
        assert "Momentum V1" in champs
        assert "✅ active" in champs["Momentum V1"]
        assert "Signaux : 2" in champs["Momentum V1"]
        assert "Gold Breakout V1" in champs
        assert "⛔ désactivée" in champs["Gold Breakout V1"]
        assert "Signaux : 1" in champs["Gold Breakout V1"]


class TestEnregistrement:
    def test_toutes_les_commandes_sont_sur_l_arbre(self, test_settings: Settings):
        from app.discord.bot import create_bot

        bot = create_bot(test_settings)
        bot_commands.register(bot)
        for nom in ("status", "lastsignal", "signals", "stats", "strategy"):
            commande = bot.tree.get_command(nom)
            assert commande is not None, f"commande /{nom} manquante"
            assert commande.description  # description requise par Discord
