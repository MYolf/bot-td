"""Schéma Pydantic des signaux TradingView (Phase 6).

TradingView envoie les valeurs numériques sous forme de chaînes
(ex. "104532.42" avec {{close}}) : Pydantic les convertit automatiquement.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TradingViewSignal(BaseModel):
    """Signal reçu par le webhook POST /webhook/tradingview."""

    secret: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    action: Literal["BUY", "SELL"]
    price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    timestamp: datetime

    @field_validator("strategy", "symbol", "exchange", "timeframe", mode="after")
    @classmethod
    def strip_and_upper(cls, value: str) -> str:
        """Normalise : sans espaces superflus, en majuscules (sauf la stratégie en minuscules gérée au validator)."""
        return value.strip()

    @field_validator("symbol", "exchange", mode="after")
    @classmethod
    def to_upper(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("strategy", mode="after")
    @classmethod
    def to_lower(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("timeframe", mode="after")
    @classmethod
    def normalize_timeframe(cls, value: str) -> str:
        """TradingView peut envoyer "60" ou "1H" : on normalise vers les codes internes."""
        mapping = {
            "1": "1", "5": "5", "15": "15", "30": "30",
            "60": "60", "1H": "60", "240": "240", "4H": "240",
            "D": "D", "1D": "D",
        }
        key = value.strip().upper()
        return mapping.get(key, key)
