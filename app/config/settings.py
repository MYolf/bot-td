"""Configuration centralisée typée du projet bot-td.

Toutes les valeurs proviennent des variables d'environnement (fichier .env),
jamais du code. Le secret du webhook et le token Discord peuvent ainsi être
remplacés sans modifier le code.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Paramètres de l'application, chargés depuis l'environnement."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # "VAR=" dans .env => valeur par défaut (None)
        extra="ignore",
    )

    # --- Application ---
    app_env: str = Field(default="development", alias="APP_ENV")

    # --- Discord ---
    discord_bot_token: str = Field(alias="DISCORD_BOT_TOKEN")
    # Permet de désactiver le bot (tests) : l'API fonctionne alors sans Discord.
    discord_enabled: bool = Field(default=True, alias="DISCORD_ENABLED")
    discord_guild_id: int | None = Field(default=None, alias="DISCORD_GUILD_ID")
    discord_signals_channel_id: int | None = Field(
        default=None, alias="DISCORD_SIGNALS_CHANNEL_ID"
    )
    discord_logs_channel_id: int | None = Field(
        default=None, alias="DISCORD_LOGS_CHANNEL_ID"
    )

    # --- Webhook TradingView ---
    tradingview_webhook_secret: str = Field(alias="TRADINGVIEW_WEBHOOK_SECRET")

    # --- Base de données ---
    database_url: str = Field(alias="DATABASE_URL")

    # --- Logs ---
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Listes blanches configurables (JSON dans les variables d'environnement) ---
    # Exemple .env : ALLOWED_SYMBOLS=["BTCUSDT","ETHUSDT","XAUUSD"]
    allowed_symbols: list[str] = Field(
        default=["BTCUSDT", "ETHUSDT", "XAUUSD"], alias="ALLOWED_SYMBOLS"
    )
    allowed_timeframes: list[str] = Field(
        default=["5", "15", "30", "60", "240", "D"], alias="ALLOWED_TIMEFRAMES"
    )
    allowed_strategies: list[str] = Field(
        default=[
            "momentum_v1",
            "momentum_mtf_v1",  # multi-timeframes (Phase 25)
            "trend_following_v2",
            "gold_breakout_v1",
        ],
        alias="ALLOWED_STRATEGIES",
    )
    allowed_exchanges: list[str] = Field(
        default=["BINANCE", "OANDA", "FXCM"], alias="ALLOWED_EXCHANGES"
    )

    # Âge maximum d'un signal (en secondes) avant rejet pour obsolescence.
    signal_max_age_seconds: int = Field(default=300, alias="SIGNAL_MAX_AGE_SECONDS")


@lru_cache
def get_settings() -> Settings:
    """Retourne l'instance unique des paramètres (mise en cache)."""
    return Settings()
