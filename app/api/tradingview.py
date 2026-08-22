"""Endpoint webhook TradingView (Phases 5, 7 à 11).

Pipeline strict : authentification (secret) -> validation métier -> cohérence
-> déduplication + stockage PostgreSQL. L'envoi Discord arrive en Phase 12.

Sécurité :
- secret comparé en temps constant (secrets.compare_digest) ;
- secret jamais loggé, jamais retourné dans une erreur.
"""

import logging
import secrets as py_secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings, get_settings
from app.database.database import provide_session_factory
from app.database.repository import SignalRepository, StrategyRepository
from app.signals.schemas import TradingViewSignal
from app.signals.validator import validate_signal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tradingview"])


@router.post("/webhook/tradingview", status_code=202)
async def receive_tradingview_signal(
    signal: TradingViewSignal,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(provide_session_factory)
    ],
) -> dict:
    logger.info(
        "TradingView webhook received strategy=%s symbol=%s timeframe=%s action=%s",
        signal.strategy,
        signal.symbol,
        signal.timeframe,
        signal.action,
    )

    # --- Phase 7 : authentification ---
    if not py_secrets.compare_digest(signal.secret, settings.tradingview_webhook_secret):
        logger.warning("TradingView webhook rejected: invalid secret")
        raise HTTPException(status_code=401, detail="Unauthorized")

    # --- Phases 8-9 : validation métier et cohérence ---
    result = validate_signal(signal, settings)
    if not result.valid:
        logger.warning(
            "Signal rejected reason=%s strategy=%s symbol=%s timeframe=%s action=%s",
            result.reason,
            signal.strategy,
            signal.symbol,
            signal.timeframe,
            signal.action,
        )
        raise HTTPException(status_code=400, detail={"error": "signal_rejected", "reason": result.reason})

    logger.info("Signal validated strategy=%s symbol=%s action=%s", signal.strategy, signal.symbol, signal.action)

    # --- Phases 10-11 : stockage + déduplication (contrainte UNIQUE signal_uid) ---
    # La session est ouverte puis fermée ici, avant toute notification (Phase 12).
    try:
        async with session_factory() as session:
            try:
                strategy = await StrategyRepository(session).get_or_create(signal.strategy)
                insert = await SignalRepository(session).insert_validated(signal, strategy.id)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except HTTPException:
        raise
    except Exception:
        # Erreur base de données : on logge tout le contexte métier (jamais le
        # secret) pour ne rien perdre, et on répond 503 à TradingView.
        logger.exception(
            "Erreur base de données signal strategy=%s symbol=%s timeframe=%s action=%s price=%s sl=%s tp=%s timestamp=%s",
            signal.strategy,
            signal.symbol,
            signal.timeframe,
            signal.action,
            signal.price,
            signal.stop_loss,
            signal.take_profit,
            signal.timestamp.isoformat(),
        )
        response.status_code = 503
        return {"status": "error", "error": "database_unavailable"}

    if insert.duplicate:
        # Un doublon ne doit JAMAIS générer deux messages Discord.
        logger.info("Signal dupliqué ignoré signal_uid=%s", insert.signal_uid)
        return {"status": "duplicate"}

    logger.info("Signal stocké id=%s signal_uid=%s", insert.signal_id, insert.signal_uid)
    return {"status": "accepted", "signal_id": insert.signal_id}
