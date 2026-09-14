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
    # Salon du suivi des trades : clôtures TP/SL en direct + récap quotidien.
    discord_recap_channel_id: int | None = Field(
        default=None, alias="DISCORD_RECAP_CHANNEL_ID"
    )
    # Salon des rappels break-even (+1,5R atteint -> SL à l'entrée).
    discord_be_channel_id: int | None = Field(
        default=None, alias="DISCORD_BE_CHANNEL_ID"
    )
    # Salon des clôtures SL (le salon récap ne reçoit que le récap hebdo).
    discord_sl_channel_id: int | None = Field(
        default=None, alias="DISCORD_SL_CHANNEL_ID"
    )
    # Salon des clôtures TP + rappels de sorties partielles TP1/TP2.
    discord_tp_channel_id: int | None = Field(
        default=None, alias="DISCORD_TP_CHANNEL_ID"
    )
    # Salon dédié des signaux en contexte macro HIGH/EXTREME (Macro Risk
    # Engine display-only, MACRO.md §10) : le signal y est routé À LA PLACE
    # du salon des signaux. Non configuré -> salon des signaux habituel.
    discord_macro_channel_id: int | None = Field(
        default=None, alias="DISCORD_MACRO_CHANNEL_ID"
    )
    # Salon dédié des pré-alertes (signaux à l'avance du moteur local :
    # niveau limite annoncé pendant la bougie en formation + annulation si
    # touché non confirmé). Non configuré -> salon des signaux habituel.
    discord_advance_channel_id: int | None = Field(
        default=None, alias="DISCORD_ADVANCE_CHANNEL_ID"
    )

    # --- Récap hebdomadaire du paper trading (vendredi par défaut) ---
    recap_enabled: bool = Field(default=True, alias="RECAP_ENABLED")
    # Jour d'envoi (date.weekday() : lundi=0 ... dimanche=6 ; 4 = vendredi).
    recap_weekday: int = Field(default=4, alias="RECAP_WEEKDAY")
    # Heure d'envoi, dans le fuseau recap_timezone (heure locale).
    recap_hour: int = Field(default=22, alias="RECAP_HOUR")
    recap_timezone: str = Field(default="Europe/Paris", alias="RECAP_TIMEZONE")

    # --- Alertes de santé (salon DISCORD_LOGS_CHANNEL_ID) ---
    health_enabled: bool = Field(default=True, alias="HEALTH_ENABLED")
    # Période entre deux cycles de contrôle (secondes).
    health_interval_seconds: int = Field(default=300, alias="HEALTH_INTERVAL_SECONDS")
    # Silence maximum du moteur (aucune bougie via /internal/prices) avant
    # alerte : au moins deux fois le plus grand timeframe suivi.
    health_engine_max_silence_seconds: int = Field(
        default=1800, alias="HEALTH_ENGINE_MAX_SILENCE_SECONDS"
    )
    # Cascade d'erreurs : fenêtre glissante et seuil d'alerte.
    health_error_window_seconds: int = Field(default=3600, alias="HEALTH_ERROR_WINDOW_SECONDS")
    health_error_max: int = Field(default=3, alias="HEALTH_ERROR_MAX")

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
