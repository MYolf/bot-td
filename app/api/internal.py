"""Endpoint interne de mises à jour de prix (conteneur engine -> backend).

À chaque bougie fermée, le moteur local (`engine/`) POSTe l'OHLC de la bougie
ici : le paper trading peut alors clôturer les positions au TP/SL sans attendre
le signal suivant. Même secret partagé que le webhook TradingView, mêmes
garanties (comparaison en temps constant, secret jamais loggé).

Frontière non fiable : validation stricte de l'entrée. Une position n'est
JAMAIS vérifiée contre la bougie qui l'a créée (anti-lookahead, voir
`PaperTradingEngine.check_candle`).
"""

import logging
import secrets as py_secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings, get_settings
from app.database.database import provide_session_factory
from app.paper_trading.engine import PaperTradingEngine, provide_paper_engine
from app.services.discord_service import (
    SignalNotifier,
    notify_be,
    notify_closure,
    provide_be_notifier,
    provide_recap_notifier,
)
from app.services.health_monitor import record_price_update

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])


class PriceUpdate(BaseModel):
    """Bougie fermée envoyée par le moteur local (temps en ms Unix, Binance)."""

    secret: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    open_time: int = Field(ge=0)  # ms Unix, début de la bougie
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)

    @field_validator("symbol", mode="after")
    @classmethod
    def to_upper(cls, value: str) -> str:
        return value.strip().upper()


@router.post("/internal/prices", status_code=200)
async def receive_price_update(
    update: PriceUpdate,
    settings: Annotated[Settings, Depends(get_settings)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(provide_session_factory)
    ],
    paper_engine: Annotated[PaperTradingEngine | None, Depends(provide_paper_engine)],
    recap_notifier: Annotated[SignalNotifier | None, Depends(provide_recap_notifier)],
    be_notifier: Annotated[SignalNotifier | None, Depends(provide_be_notifier)],
) -> dict:
    logger.info(
        "Price update received symbol=%s timeframe=%s open_time=%s",
        update.symbol,
        update.timeframe,
        update.open_time,
    )

    # --- Authentification (même secret partagé que le webhook TradingView) ---
    if not py_secrets.compare_digest(update.secret, settings.tradingview_webhook_secret):
        logger.warning("Price update rejected: invalid secret")
        raise HTTPException(status_code=401, detail="Unauthorized")

    # --- Validation métier : liste blanche des symboles ---
    if update.symbol not in settings.allowed_symbols:
        logger.warning("Price update rejected: symbol not allowed symbol=%s", update.symbol)
        return {"status": "rejected", "reason": "symbol_not_allowed"}

    # Heartbeat pour les alertes de santé : prouve que le moteur est vivant.
    record_price_update()

    if paper_engine is None:
        return {"status": "ok", "closed": 0}

    candle_start = datetime.fromtimestamp(update.open_time / 1000, tz=timezone.utc)
    try:
        closed = await paper_engine.check_candle(
            symbol=update.symbol,
            high=Decimal(str(update.high)),
            low=Decimal(str(update.low)),
            candle_start=candle_start,
        )
    except Exception:
        # Best-effort : jamais d'impact sur le reste, erreur loggée.
        logger.exception(
            "Vérification paper trading échouée symbol=%s open_time=%s",
            update.symbol,
            update.open_time,
        )
        return {"status": "error", "error": "check_failed"}

    for outcome in closed:
        await notify_closure(outcome, recap_notifier)

    # Rappels break-even (+1,5R) : best-effort, après les clôtures (une
    # position clôturée à cette même bougie ne déclenche pas d'alerte BE).
    try:
        be_alerts = await paper_engine.check_break_even(
            symbol=update.symbol,
            high=Decimal(str(update.high)),
            low=Decimal(str(update.low)),
            candle_start=candle_start,
        )
    except Exception:
        logger.exception(
            "Vérification break-even échouée symbol=%s open_time=%s (ignoré)",
            update.symbol,
            update.open_time,
        )
        be_alerts = []
    for alert in be_alerts:
        await notify_be(alert, be_notifier)

    return {"status": "ok", "closed": len(closed)}
