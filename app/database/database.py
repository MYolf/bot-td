"""Engine async SQLAlchemy et fabrique de sessions (Phase 11).

L'engine est créé dans le lifespan FastAPI et dispose proprement à l'arrêt.
Les endpoints obtiennent une session via la dépendance FastAPI `get_session`,
ce qui permet aux tests de remplacer la fabrique (SQLite en mémoire).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> None:
    """Crée l'engine et la fabrique de sessions globaux."""
    global _engine, _session_factory
    _engine = create_async_engine(database_url, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    logger.info("Engine base de données initialisé")


async def dispose_engine() -> None:
    """Ferme l'engine à l'arrêt de l'application."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("Engine base de données fermé")
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Fabrique de sessions courante (remplacée dans les tests)."""
    if _session_factory is None:
        raise RuntimeError("Engine base de données non initialisé")
    return _session_factory


def set_session_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    """Injecte une fabrique alternative (tests uniquement)."""
    global _session_factory
    _session_factory = factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Une unité de travail = une session, commitée si tout va bien.

    Ne jamais garder une session ouverte à travers un appel réseau Discord :
    ouvrir, écrire, fermer AVANT la notification.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def provide_session_factory() -> async_sessionmaker[AsyncSession]:
    """Dépendance FastAPI : la fabrique de sessions courante.

    Les tests surchargent cette dépendance (app.dependency_overrides) pour
    brancher un SQLite async en mémoire.
    """
    return get_session_factory()
