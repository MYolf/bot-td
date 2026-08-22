"""Endpoint webhook TradingView (Phases 5 et 7).

Pipeline strict : authentification (secret) -> validation métier -> accepté.
Les étapes suivantes (déduplication, PostgreSQL, Discord) seront branchées ici
dans les phases 10 à 12.

Sécurité :
- secret comparé en temps constant (secrets.compare_digest) ;
- secret jamais loggé, jamais retourné dans une erreur.
"""

import logging
import secrets as py_secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from app.config.settings import Settings, get_settings
from app.signals.schemas import TradingViewSignal
from app.signals.validator import validate_signal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tradingview"])


@router.post("/webhook/tradingview", status_code=202)
async def receive_tradingview_signal(
    signal: TradingViewSignal,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
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

    # Phases 10-12 : déduplication, stockage PostgreSQL, envoi Discord (à venir).
    response.status_code = 202
    return {"status": "accepted"}
