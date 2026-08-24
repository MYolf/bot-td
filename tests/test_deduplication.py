"""Tests de la déduplication et de la persistance (Phases 10-11)."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models import Base, Signal, Strategy
from app.database.repository import (
    SignalRepository,
    StrategyRepository,
    compute_risk_reward,
)
from app.signals.deduplication import build_signal_uid
from app.signals.schemas import TradingViewSignal


def _signal(timestamp: datetime | None = None, action: str = "BUY") -> TradingViewSignal:
    return TradingViewSignal(
        secret="secret-test",
        strategy="momentum_v1",
        symbol="BTCUSDT",
        exchange="BINANCE",
        timeframe="15",
        action=action,
        price=104532.42,
        stop_loss=103800.00,
        take_profit=106000.00,
        timestamp=timestamp or datetime.now(timezone.utc),
    )


class TestSignalUid:
    """Format imposé par Projet.md §16 : strategy:symbol:timeframe:timestamp:action."""

    def test_format_exemple_projet(self):
        ts = datetime.fromtimestamp(1755892800, tz=timezone.utc)
        uid = build_signal_uid("momentum_v1", "BTCUSDT", "15", ts, "BUY")
        assert uid == "momentum_v1:BTCUSDT:15:1755892800:BUY"

    def test_timestamp_naif_considere_utc(self):
        naive = datetime.utcfromtimestamp(1755892800)
        uid = build_signal_uid("momentum_v1", "BTCUSDT", "15", naive, "BUY")
        assert uid == "momentum_v1:BTCUSDT:15:1755892800:BUY"

    def test_meme_bougie_meme_uid(self):
        ts = datetime.now(timezone.utc).replace(microsecond=0)
        assert build_signal_uid("a", "B", "15", ts, "BUY") == build_signal_uid(
            "a", "B", "15", ts, "BUY"
        )

    def test_bougies_differentes_uid_differents(self):
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert build_signal_uid("a", "B", "15", ts, "BUY") != build_signal_uid(
            "a", "B", "15", ts + timedelta(minutes=15), "BUY"
        )


class TestRiskReward:
    def test_buy_exemple_projet(self):
        signal = _signal()
        # entry 104532.42, SL 103800, TP 106000 → risk 732.42, reward 1467.58
        rr = compute_risk_reward(signal)
        assert rr == Decimal("2.0037")  # 1467.58 / 732.42

    def test_sell_inverse(self):
        signal = _signal(action="SELL")
        # SELL : risk = SL - entry, reward = entry - TP → négatif ici, mais la
        # cohérence est validée en amont ; on vérifie juste le calcul.
        entry = Decimal(str(signal.price))
        expected = ((entry - Decimal(str(signal.take_profit))) / (
            Decimal(str(signal.stop_loss)) - entry
        )).quantize(Decimal("0.0001"))
        assert compute_risk_reward(signal) == expected


class TestRepositoryDeduplication:
    """Insertion + doublon détecté par la contrainte UNIQUE (via SQLite async)."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_premier_insere_second_duplique(self):
        async def scenario() -> tuple[bool, bool, int]:
            from sqlalchemy.ext.asyncio import create_async_engine
            from sqlalchemy.pool import StaticPool

            engine = create_async_engine(
                "sqlite+aiosqlite://",
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
            )
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory: async_sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

            ts = datetime.now(timezone.utc).replace(microsecond=0)
            async with factory() as session:
                strategy = await StrategyRepository(session).get_or_create("momentum_v1")
                premier = await SignalRepository(session).insert_validated(_signal(ts), strategy.id)
                await session.commit()

            async with factory() as session:
                strategy = await StrategyRepository(session).get_or_create("momentum_v1")
                second = await SignalRepository(session).insert_validated(_signal(ts), strategy.id)
                await session.commit()

            async with factory() as session:
                total = len((await session.execute(select(Signal))).scalars().all())
                strategies = len((await session.execute(select(Strategy))).scalars().all())

            await engine.dispose()
            return premier.duplicate, second.duplicate, total

        premier_duplique, second_duplique, total = self._run(scenario())
        assert premier_duplique is False
        assert second_duplique is True
        # Un seul signal stocké : jamais deux messages Discord.
        assert total == 1

    def test_get_or_create_strategy(self):
        async def scenario() -> tuple[int, int]:
            from sqlalchemy.ext.asyncio import create_async_engine
            from sqlalchemy.pool import StaticPool

            engine = create_async_engine(
                "sqlite+aiosqlite://",
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
            )
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory: async_sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

            async with factory() as session:
                s1 = await StrategyRepository(session).get_or_create("momentum_v1")
                await session.commit()
            async with factory() as session:
                s2 = await StrategyRepository(session).get_or_create("momentum_v1")
                await session.commit()
            async with factory() as session:
                total = len((await session.execute(select(Strategy))).scalars().all())

            await engine.dispose()
            return s1.id, total

        premier_id, total = self._run(scenario())
        assert total == 1  # réutilisation, pas de création double


class TestWebhookDeduplication:
    """Le webhook lui-même doit ignorer un signal envoyé deux fois."""

    def test_deux_envois_meme_signal(self, client):
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
        premier = client.post("/webhook/tradingview", json=payload)
        second = client.post("/webhook/tradingview", json=payload)

        assert premier.status_code == 200
        assert premier.json()["status"] == "sent"
        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"

    def test_deux_signaux_differents_acceptes(self, client):
        base = {
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
        autre = dict(base, timestamp=(datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat())
        assert client.post("/webhook/tradingview", json=base).json()["status"] == "sent"
        assert client.post("/webhook/tradingview", json=autre).json()["status"] == "sent"
