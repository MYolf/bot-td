"""Tests de la validation métier et de cohérence (Phases 8 et 9)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import Settings
from app.signals.schemas import TradingViewSignal
from app.signals.validator import validate_signal


def _make_signal(**overrides) -> TradingViewSignal:
    payload = {
        "secret": "s",
        "strategy": "momentum_v1",
        "symbol": "BTCUSDT",
        "exchange": "BINANCE",
        "timeframe": "15",
        "action": "BUY",
        "price": 100.0,
        "stop_loss": 98.0,
        "take_profit": 104.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(overrides)
    return TradingViewSignal(**payload)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        DISCORD_BOT_TOKEN="t",
        TRADINGVIEW_WEBHOOK_SECRET="s",
        DATABASE_URL="postgresql+psycopg://u:p@localhost/db",
    )


def test_valid_buy(settings):
    assert validate_signal(_make_signal(), settings).valid


def test_valid_sell(settings):
    signal = _make_signal(action="SELL", stop_loss=102.0, take_profit=96.0)
    assert validate_signal(signal, settings).valid


def test_buy_stop_loss_above_entry(settings):
    result = validate_signal(_make_signal(stop_loss=101.0), settings)
    assert not result.valid
    assert result.reason == "incoherent_stop_loss"


def test_buy_take_profit_below_entry(settings):
    result = validate_signal(_make_signal(take_profit=99.0), settings)
    assert not result.valid
    assert result.reason == "incoherent_take_profit"


def test_sell_stop_loss_below_entry(settings):
    result = validate_signal(_make_signal(action="SELL", stop_loss=99.0, take_profit=96.0), settings)
    assert not result.valid
    assert result.reason == "incoherent_stop_loss"


def test_sell_take_profit_above_entry(settings):
    result = validate_signal(_make_signal(action="SELL", stop_loss=102.0, take_profit=105.0), settings)
    assert not result.valid
    assert result.reason == "incoherent_take_profit"


def test_strategy_not_whitelisted(settings):
    result = validate_signal(_make_signal(strategy="inconnue_v9"), settings)
    assert result.reason == "invalid_strategy"


def test_symbol_not_whitelisted(settings):
    result = validate_signal(_make_signal(symbol="DOGEUSDT"), settings)
    assert result.reason == "invalid_symbol"


def test_exchange_not_whitelisted(settings):
    result = validate_signal(_make_signal(exchange="UNKNOWN"), settings)
    assert result.reason == "invalid_exchange"


def test_timeframe_not_whitelisted(settings):
    result = validate_signal(_make_signal(timeframe="3"), settings)
    assert result.reason == "invalid_timeframe"


def test_expired_timestamp(settings):
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    result = validate_signal(_make_signal(timestamp=old), settings)
    assert result.reason == "expired_timestamp"


def test_future_timestamp(settings):
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    result = validate_signal(_make_signal(timestamp=future), settings)
    assert result.reason == "future_timestamp"
