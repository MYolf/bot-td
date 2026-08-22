"""Application FastAPI principale (Phase 4).

Point d'entrée : uvicorn app.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.tradingview import router as tradingview_router
from app.config.settings import get_settings
from app.utils.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("Application démarrée environnement=%s", settings.app_env)
    yield
    logger.info("Application arrêtée")


app = FastAPI(
    title="bot-td",
    description="Bot Discord de signaux de trading (TradingView) — signalisation uniquement, jamais d'ordre.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(tradingview_router)


@app.get("/health")
async def health() -> dict:
    """Endpoint de santé (Phase 4)."""
    return {"status": "ok"}
