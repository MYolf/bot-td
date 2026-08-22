"""Application FastAPI principale (Phase 4).

Point d'entrée : uvicorn app.main:app --reload
Le bot Discord (Phase 3) démarre dans le même process, via le lifespan.
"""

import asyncio
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

    bot_task: asyncio.Task | None = None
    if settings.discord_enabled:
        from app.discord.bot import create_bot, run_bot

        bot = create_bot(settings)
        # Un seul loop : celui de FastAPI/Uvicorn, pas de thread séparé.
        bot_task = asyncio.create_task(
            run_bot(bot, settings.discord_bot_token),
            name="discord-bot",
        )
        logger.info("Démarrage du bot Discord en tâche de fond")
    else:
        logger.info("Bot Discord désactivé (DISCORD_ENABLED=false)")

    yield

    if bot_task is not None:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        logger.info("Bot Discord arrêté")
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
