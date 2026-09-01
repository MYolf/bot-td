"""Application FastAPI principale (Phase 4).

Point d'entrée : uvicorn app.main:app --reload
Le bot Discord (Phase 3) démarre dans le même process, via le lifespan.
"""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Windows : psycopg async exige un SelectorEventLoop (uvicorn choisirait sinon
# le ProactorEventLoop par défaut, incompatible). À faire avant toute création
# de loop, donc au moment de l'import.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def selector_loop() -> asyncio.SelectorEventLoop:
    """Loop compatible psycopg async sur Windows.

    À utiliser au lancement : uvicorn app.main:app --loop app.main:selector_loop
    (uvicorn force sinon un ProactorEventLoop sur Windows, incompatible psycopg).
    """
    return asyncio.SelectorEventLoop()

from app.api.internal import router as internal_router
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
    recap_task: asyncio.Task | None = None
    if settings.discord_enabled:
        from app.discord.bot import create_bot, run_bot
        from app.services.discord_service import (
            DiscordService,
            set_notifier,
            set_recap_notifier,
        )

        bot = create_bot(settings)
        # Un seul loop : celui de FastAPI/Uvicorn, pas de thread séparé.
        bot_task = asyncio.create_task(
            run_bot(bot, settings.discord_bot_token),
            name="discord-bot",
        )
        # Notifieur utilisé par le pipeline des signaux (Phase 12) : le bot
        # peut mettre quelques secondes à se connecter, is_ready() est
        # vérifié à chaque envoi.
        set_notifier(
            DiscordService(bot, settings.discord_signals_channel_id)
        )
        # Notifieur du salon récap : clôtures TP/SL en direct + récap quotidien.
        if settings.discord_recap_channel_id is not None:
            set_recap_notifier(
                DiscordService(bot, settings.discord_recap_channel_id)
            )
        logger.info("Démarrage du bot Discord en tâche de fond")
    else:
        logger.info("Bot Discord désactivé (DISCORD_ENABLED=false)")

    # --- Phase 11 : engine base de données ---
    from app.database.database import dispose_engine, get_session_factory, init_engine

    init_engine(settings.database_url)

    # --- Phase 21 : moteur de paper trading (simulation locale) ---
    from app.paper_trading.engine import init_paper_engine, shutdown_paper_engine

    init_paper_engine(get_session_factory())

    # --- Récap hebdomadaire (vendredi 22h heure locale par défaut) ---
    if settings.discord_enabled and settings.recap_enabled:
        from app.services.discord_service import provide_recap_notifier
        from app.services.weekly_recap import WeeklyRecapService

        recap_notifier = provide_recap_notifier()
        if recap_notifier is None:
            logger.warning(
                "Récap hebdomadaire activé mais DISCORD_RECAP_CHANNEL_ID non configuré (désactivé)"
            )
        else:
            recap_task = asyncio.create_task(
                WeeklyRecapService(
                    get_session_factory(),
                    recap_notifier,
                    hour=settings.recap_hour,
                    weekday=settings.recap_weekday,
                    timezone_name=settings.recap_timezone,
                ).run(),
                name="weekly-recap",
            )
            logger.info("Démarrage du récap hebdomadaire en tâche de fond")

    yield

    if recap_task is not None:
        recap_task.cancel()
        try:
            await recap_task
        except asyncio.CancelledError:
            pass
        logger.info("Récap hebdomadaire arrêté")
    shutdown_paper_engine()

    if bot_task is not None:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        logger.info("Bot Discord arrêté")
    from app.services.discord_service import set_notifier, set_recap_notifier

    set_notifier(None)
    set_recap_notifier(None)
    await dispose_engine()
    logger.info("Application arrêtée")


app = FastAPI(
    title="bot-td",
    description="Bot Discord de signaux de trading (TradingView) — signalisation uniquement, jamais d'ordre.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(tradingview_router)
app.include_router(internal_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Phase 18 : toute exception non gérée => 500 + log ERROR.

    Réponse générique sans détail d'exception (jamais de stacktrace ni de
    secret côté client) ; le détail complet va dans les logs serveur.
    """
    logger.exception("Exception non gérée path=%s : %s", request.url.path, type(exc).__name__)
    return JSONResponse(status_code=500, content={"status": "error", "error": "internal_error"})


@app.get("/health")
async def health() -> dict:
    """Endpoint de santé (Phase 4)."""
    return {"status": "ok"}
