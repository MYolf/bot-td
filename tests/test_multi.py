"""Tests multi-actifs, multi-timeframes, multi-stratégies (Phases 22-24).

Le support vient de la configuration (listes blanches du .env), pas du code :
les tests prouvent que plusieurs actifs/timeframes/stratégies circulent dans
tout le pipeline (webhook -> base -> paper trading) et que les statistiques
sont filtrables par timeframe et par stratégie (exigence Phase 23).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config.settings import Settings
from app.database.database import set_session_factory
from app.database.models import Base, Signal, Strategy
from app.discord import commands as bot_commands
from tests.test_discord_bot import FakeInteraction


def _payload(**overrides) -> dict:
    payload = {
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
    payload.update(overrides)
    return payload


def _signals_en_base(client) -> list[Signal]:
    async def load():
        async with client.db_factory() as session:
            return list((await session.execute(select(Signal))).scalars().all())

    return asyncio.run(load())


# --- Phase 22 : multi-actifs ---

class TestMultiActifs:
    def test_trois_actifs_acceptes_sans_modification_du_code(self, client):
        for symbol in ("BTCUSDT", "ETHUSDT", "XAUUSD"):
            response = client.post(
                "/webhook/tradingview", json=_payload(symbol=symbol)
            )
            assert response.status_code == 200
            assert response.json()["status"] == "sent"
        symbols = {s.symbol for s in _signals_en_base(client)}
        assert symbols == {"BTCUSDT", "ETHUSDT", "XAUUSD"}

    def test_actif_hors_configuration_rejete(self, client):
        response = client.post("/webhook/tradingview", json=_payload(symbol="EURUSD"))
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert response.json()["reason"] == "invalid_symbol"

    def test_positions_paper_par_actif(self, client):
        """Le paper trading suit chaque actif indépendamment (Phase 21+22)."""
        for symbol in ("BTCUSDT", "ETHUSDT"):
            client.post("/webhook/tradingview", json=_payload(symbol=symbol))
        from app.database.models import PaperPosition

        async def load():
            async with client.db_factory() as session:
                return list(
                    (await session.execute(select(PaperPosition))).scalars().all()
                )

        assert len(asyncio.run(load())) == 2


# --- Phase 23 : multi-timeframes ---

class TestMultiTimeframes:
    def test_timeframes_configures_acceptes(self, client):
        for timeframe in ("5", "15", "30", "60", "240", "D"):
            response = client.post(
                "/webhook/tradingview", json=_payload(timeframe=timeframe)
            )
            assert response.status_code == 200
            assert response.json()["status"] == "sent"
        timeframes = {s.timeframe for s in _signals_en_base(client)}
        assert timeframes == {"5", "15", "30", "60", "240", "D"}

    def test_timeframe_enregistre_avec_chaque_signal(self, client):
        client.post("/webhook/tradingview", json=_payload(timeframe="240"))
        (signal,) = _signals_en_base(client)
        assert signal.timeframe == "240"

    def test_timeframe_hors_configuration_rejete(self, client):
        response = client.post("/webhook/tradingview", json=_payload(timeframe="3"))
        assert response.json()["reason"] == "invalid_timeframe"


# --- Phase 24 : multi-stratégies ---

class TestMultiStrategies:
    def test_strategies_configurees_acceptees(self, client):
        for strategy in ("momentum_v1", "trend_following_v2", "gold_breakout_v1"):
            response = client.post(
                "/webhook/tradingview", json=_payload(strategy=strategy)
            )
            assert response.status_code == 200
            assert response.json()["status"] == "sent"

        async def strategies_en_base():
            async with client.db_factory() as session:
                return list(
                    (await session.execute(select(Strategy))).scalars().all()
                )

        noms = {s.name for s in asyncio.run(strategies_en_base())}
        assert noms == {"momentum_v1", "trend_following_v2", "gold_breakout_v1"}

    def test_strategie_hors_configuration_rejetee(self, client):
        response = client.post(
            "/webhook/tradingview", json=_payload(strategy="trend_v1")
        )
        assert response.json()["reason"] == "invalid_strategy"


# --- Phase 25 : analyse multi-timeframe (stratégie momentum_mtf_v1) ---

class TestAnalyseMultiTimeframe:
    def test_signal_mtf_pipeline_complet(self, client):
        """Le JSON exact produit par pine/momentum_mtf_v1.pine traverse tout
        le pipeline : accepté, stocké, notifié, position paper ouverte.

        Le timeframe envoyé par la stratégie est celui du graphique (entrée,
        ex. 15m) : les timeframes supérieurs sont évalués dans le Pine.
        """
        from app.database.models import PaperPosition

        response = client.post(
            "/webhook/tradingview",
            json=_payload(
                strategy="momentum_mtf_v1",
                timeframe="15",
                action="SELL",
                price="104100.50",
                stop_loss="104800.00",
                take_profit="103500.00",
            ),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "sent"

        (signal,) = _signals_en_base(client)
        assert signal.timeframe == "15"
        assert signal.action == "SELL"
        assert signal.status == "SENT"

        async def positions():
            async with client.db_factory() as session:
                return list(
                    (await session.execute(select(PaperPosition))).scalars().all()
                )

        assert len(asyncio.run(positions())) == 1

    def test_trois_niveaux_coexistent_sans_collision(self, client):
        """Tendance (240), confirmation (60) et entrée (15) d'un même
        symbole coexistent en base : le signal_uid inclut le timeframe, donc
        jamais de collision de déduplication entre les niveaux.
        """
        for timeframe in ("240", "60", "15"):
            response = client.post(
                "/webhook/tradingview",
                json=_payload(strategy="momentum_mtf_v1", timeframe=timeframe),
            )
            assert response.json()["status"] == "sent"

        signaux = _signals_en_base(client)
        assert {s.timeframe for s in signaux} == {"240", "60", "15"}
        assert len({s.signal_uid for s in signaux}) == 3


# --- Phases 23-24 : statistiques filtrables ---

def _seed_signal(
    session,
    signal_id: int,
    strategy_id: int,
    symbol: str,
    timeframe: str,
    moment: datetime,
) -> None:
    session.add(
        Signal(
            id=signal_id,
            signal_uid=f"s{signal_id}:{symbol}:{timeframe}:{int(moment.timestamp())}:BUY",
            strategy_id=strategy_id,
            symbol=symbol,
            exchange="BINANCE",
            timeframe=timeframe,
            action="BUY",
            entry_price=Decimal("100"),
            stop_loss=Decimal("98"),
            take_profit=Decimal("104"),
            risk_reward=Decimal("2"),
            signal_timestamp=moment,
            received_at=moment,
            status="SENT",
        )
    )


@pytest.fixture
def db_factory():
    factory, engine = asyncio.run(_create_factory())

    async def seed():
        async with factory() as session:
            momentum = Strategy(name="momentum_v1")
            gold = Strategy(name="gold_breakout_v1")
            session.add_all([momentum, gold])
            await session.flush()
            base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
            _seed_signal(session, 1, momentum.id, "BTCUSDT", "15", base)
            _seed_signal(session, 2, momentum.id, "ETHUSDT", "60", base + timedelta(minutes=10))
            _seed_signal(session, 3, gold.id, "XAUUSD", "15", base + timedelta(minutes=20))
            await session.commit()

    asyncio.run(seed())
    set_session_factory(factory)
    yield factory
    asyncio.run(engine.dispose())
    set_session_factory(None)  # type: ignore[arg-type]


async def _create_factory() -> tuple[async_sessionmaker, object]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


class TestStatsFiltrees:
    async def _champs(self, test_settings, **kwargs) -> dict:
        interaction = FakeInteraction(test_settings)
        await bot_commands.stats_command.callback(interaction, **kwargs)  # type: ignore[attr-defined]
        (appel,) = interaction.response.sent
        assert appel["ephemeral"] is True
        return {f.name: f.value for f in appel["embed"].fields}

    async def test_sans_filtre_tout(self, test_settings: Settings, db_factory):
        champs = await self._champs(test_settings)
        assert champs["Total"] == "3"

    async def test_filtre_timeframe(self, test_settings: Settings, db_factory):
        champs = await self._champs(test_settings, timeframe="15")
        assert champs["Total"] == "2"  # momentum 15m + gold 15m
        assert "Momentum V1: 1" in champs["Par stratégie"]
        assert "Gold Breakout V1: 1" in champs["Par stratégie"]

    async def test_filtre_strategie(self, test_settings: Settings, db_factory):
        champs = await self._champs(test_settings, strategie="momentum_v1")
        assert champs["Total"] == "2"  # 15m + 60m

    async def test_filtre_combine(self, test_settings: Settings, db_factory):
        interaction = FakeInteraction(test_settings)
        await bot_commands.stats_command.callback(  # type: ignore[attr-defined]
            interaction, timeframe="15", strategie="momentum_v1"
        )
        (appel,) = interaction.response.sent
        champs = {f.name: f.value for f in appel["embed"].fields}
        assert champs["Total"] == "1"
        assert appel["embed"].description == "Filtre : timeframe=15 · stratégie=momentum_v1"

    async def test_timeframe_inconnu_message_d_erreur(self, test_settings: Settings, db_factory):
        interaction = FakeInteraction(test_settings)
        await bot_commands.stats_command.callback(interaction, timeframe="7")  # type: ignore[attr-defined]
        (appel,) = interaction.response.sent
        assert appel["embed"] is None
        assert "Timeframe inconnu" in appel["content"]
        assert "`15`" in appel["content"]  # valeurs autorisées listées

    async def test_strategie_inconnue_message_d_erreur(self, test_settings: Settings, db_factory):
        interaction = FakeInteraction(test_settings)
        await bot_commands.stats_command.callback(interaction, strategie="trend_v1")  # type: ignore[attr-defined]
        (appel,) = interaction.response.sent
        assert "Stratégie inconnue" in appel["content"]
