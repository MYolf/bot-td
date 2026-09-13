"""Configuration du moteur de signaux (variables d'environnement uniquement).

Volontairement indépendant de app.config.settings : le moteur n'a besoin ni
de Discord ni de la base de données, et doit pouvoir tourner seul.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineSettings(BaseSettings):
    """Paramètres du moteur, chargés depuis l'environnement (.env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    engine_enabled: bool = Field(default=True, alias="ENGINE_ENABLED")
    engine_symbols: list[str] = Field(
        default=["BTCUSDT", "ETHUSDT"], alias="ENGINE_SYMBOLS"
    )
    engine_timeframe: str = Field(default="15", alias="ENGINE_TIMEFRAME")
    # Cadence de sondage : 45 s = détection d'une clôture 15m en < 1 minute,
    # très en deçà de SIGNAL_MAX_AGE_SECONDS (300) côté backend.
    engine_poll_seconds: int = Field(default=45, alias="ENGINE_POLL_SECONDS")
    engine_webhook_url: str = Field(
        default="http://localhost:8000/webhook/tradingview", alias="ENGINE_WEBHOOK_URL"
    )
    # Endpoint interne du backend : chaque bougie fermée y est POSTée pour que
    # le paper trading puisse clôturer les positions au TP/SL sans attendre le
    # signal suivant. Une URL vide désactive l'envoi.
    engine_price_url: str | None = Field(
        default="http://localhost:8000/internal/prices", alias="ENGINE_PRICE_URL"
    )
    # Historique récupéré à chaque sondage : 500 >> EMA200 + amorce MACD.
    engine_candle_limit: int = Field(default=500, alias="ENGINE_CANDLE_LIMIT")
    # Filtre qualité : score minimal (tendance 20 + momentum 20 + MACD 15,
    # max 55) pour qu'une transition soit ÉMISE. 0 = tout émettre (comportement
    # historique). 45 = seuls les signaux francs (audit 2026-09-02 : seul
    # bucket positif en brut sur BTC ET ETH ; divise les signaux par ~3).
    engine_min_score: int = Field(default=0, alias="ENGINE_MIN_SCORE")

    # Clé FRED (gratuite) pour la GÉNÉRATION du planning macro et les
    # études — jamais requise au runtime du moteur (le planning est un
    # fichier versionné, MACRO.md §4). Optionnelle.
    fred_api_key: str | None = Field(default=None, alias="FRED_API_KEY")

    # Secret partagé avec le backend (jamais loggé, jamais commité).
    tradingview_webhook_secret: str = Field(alias="TRADINGVIEW_WEBHOOK_SECRET")


def get_engine_settings() -> EngineSettings:
    return EngineSettings()
