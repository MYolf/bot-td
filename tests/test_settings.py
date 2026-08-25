"""Tests de la configuration (Phase 1)."""

import json

import pytest


def _set_required_env(monkeypatch, **overrides):
    """Fournit un environnement minimal valide pour instancier Settings."""
    env = {
        "DISCORD_BOT_TOKEN": "token-test",
        "TRADINGVIEW_WEBHOOK_SECRET": "secret-test",
        "DATABASE_URL": "postgresql+psycopg://user:pass@localhost:5432/db",
    }
    env.update(overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_settings_loads_required_values(monkeypatch):
    _set_required_env(monkeypatch)
    from app.config.settings import Settings

    settings = Settings()
    assert settings.discord_bot_token == "token-test"
    assert settings.tradingview_webhook_secret == "secret-test"
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"


def test_settings_defaults_whitelists(monkeypatch):
    _set_required_env(monkeypatch)
    from app.config.settings import Settings

    settings = Settings()
    assert "BTCUSDT" in settings.allowed_symbols
    assert "15" in settings.allowed_timeframes
    assert "momentum_v1" in settings.allowed_strategies
    # Phase 25 : la stratégie multi-timeframes est autorisée par défaut.
    assert "momentum_mtf_v1" in settings.allowed_strategies


def test_settings_whitelists_configurable_via_env(monkeypatch):
    _set_required_env(
        monkeypatch,
        ALLOWED_SYMBOLS=json.dumps(["BTCUSDT", "XAUUSD"]),
        ALLOWED_TIMEFRAMES=json.dumps(["60", "D"]),
    )
    from app.config.settings import Settings

    settings = Settings()
    assert settings.allowed_symbols == ["BTCUSDT", "XAUUSD"]
    assert settings.allowed_timeframes == ["60", "D"]


def test_settings_fails_without_secret(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "token-test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost/db")
    monkeypatch.delenv("TRADINGVIEW_WEBHOOK_SECRET", raising=False)
    from app.config.settings import Settings

    with pytest.raises(Exception):
        # _env_file=None : ne pas retomber sur le .env local du développeur.
        Settings(_env_file=None)
