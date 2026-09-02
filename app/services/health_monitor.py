"""Alertes de santé automatiques (salon Discord des logs).

Vérifie périodiquement quatre composants :
- base de données (SELECT 1) ;
- bot Discord (connexion Gateway, is_ready) ;
- moteur de signaux : heartbeat horodaté par POST /internal/prices à chaque
  bougie fermée — un silence anormalement long révèle un conteneur engine
  bloqué (il logge ses erreurs mais ne crashe jamais) ;
- cascade de signaux ERROR sur la dernière fenêtre glissante.

Anti-spam : une alerte par transition OK -> KO, une résolution par retour à
OK, rien tant que l'état ne change pas. Grâces au démarrage : le bot Discord
peut mettre quelques secondes à se connecter (non contrôlé au premier cycle) ;
le moteur sans heartbeat depuis le démarrage n'alerte pas (l'app peut démarrer
avant le conteneur engine). Les autres composants sont présumés sains : une
panne présente dès le départ est alertée dès le premier cycle.

Lecture seule + notification : aucune décision, aucun ordre (règle absolue).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import discord
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repository import SignalRepository
from app.discord.embeds import build_health_alert_embed
from app.services.discord_service import SignalNotifier

logger = logging.getLogger(__name__)


# --- Heartbeat du moteur (alimenté par le endpoint interne) ---

_last_price_update: datetime | None = None


def record_price_update(now: datetime | None = None) -> None:
    """Horodate la dernière bougie reçue du moteur (appelé par /internal/prices)."""
    global _last_price_update
    _last_price_update = now or datetime.now(timezone.utc)


def last_price_update() -> datetime | None:
    """Dernier heartbeat moteur connu (None si aucune bougie depuis le démarrage)."""
    return _last_price_update


# --- Contrôles ---

@dataclass(frozen=True)
class HealthCheck:
    """Résultat d'un contrôle : composant, état, détail lisible."""

    component: str
    ok: bool
    detail: str


def evaluate_engine_silence(
    last_update: datetime | None, now: datetime, max_silence: timedelta
) -> HealthCheck:
    """Santé du moteur selon l'âge de son dernier heartbeat.

    `last_update` None (aucune bougie depuis le démarrage de l'app) = pas
    d'alerte : le conteneur engine peut légitimement démarrer après l'app.
    """
    if last_update is None:
        return HealthCheck("Moteur de signaux", True, "en attente de la première bougie")
    age = now - last_update
    minutes = int(age.total_seconds() // 60)
    if age > max_silence:
        return HealthCheck(
            "Moteur de signaux", False, f"aucune bougie reçue depuis {minutes} min"
        )
    return HealthCheck("Moteur de signaux", True, f"dernière bougie il y a {minutes} min")


class HealthAlertService:
    """Boucle de contrôle de santé (démarrée dans le lifespan)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        notifier: SignalNotifier,
        bot: discord.Client,
        *,
        interval_seconds: int = 300,
        engine_max_silence_seconds: int = 1800,
        error_window_seconds: int = 3600,
        error_max: int = 3,
    ):
        self._session_factory = session_factory
        self._notifier = notifier
        self._bot = bot
        self._interval = interval_seconds
        self._max_silence = timedelta(seconds=engine_max_silence_seconds)
        self._error_window = timedelta(seconds=error_window_seconds)
        self._error_max = error_max
        self._states: dict[str, bool] = {}
        self._cycle = 0

    async def _ping_database(self) -> bool:
        """SELECT 1 : tout échec => False, jamais d'exception remontée."""
        try:
            async with self._session_factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.warning("Ping base de données échoué (contrôle de santé)")
            return False

    async def collect_checks(self, now_utc: datetime | None = None) -> list[HealthCheck]:
        """Évalue tous les composants contrôlables à cet instant.

        Le contrôle des signaux ERROR n'est évalué que si la base répond :
        sinon il est omis (et son état précédent est conservé).
        """
        now = now_utc or datetime.now(timezone.utc)
        db_online = await self._ping_database()
        # Grâce du premier cycle pour le bot : la connexion Gateway peut
        # prendre quelques secondes après le démarrage.
        bot_online = self._bot.is_ready() or self._cycle == 0
        checks = [
            HealthCheck("Base de données", db_online, "SELECT 1"),
            HealthCheck("Bot Discord", bot_online, "connexion Gateway"),
            evaluate_engine_silence(last_price_update(), now, self._max_silence),
        ]
        if db_online:
            async with self._session_factory() as session:
                errors = await SignalRepository(session).count_errors_since(
                    now - self._error_window
                )
            checks.append(
                HealthCheck(
                    "Signaux en erreur",
                    errors < self._error_max,
                    f"{errors} signal(aux) ERROR sur la dernière heure",
                )
            )
        return checks

    async def check_once(self, now_utc: datetime | None = None) -> list[HealthCheck]:
        """Un cycle : détecte les transitions et notifie ; retourne les changements.

        Best-effort : un échec d'envoi Discord est loggé, jamais propagé (le
        cycle suivant retentera la transition, l'état n'étant pas mis à jour).
        """
        checks = await self.collect_checks(now_utc)
        changed: list[HealthCheck] = []
        notified: set[str] = set()
        for check in checks:
            # Composant non encore observé : présomption saine (une panne
            # présente dès le départ est alertée immédiatement).
            previous = self._states.get(check.component, True)
            if previous == check.ok:
                continue
            try:
                await self._notifier.send_signal(
                    build_health_alert_embed(
                        component=check.component,
                        detail=check.detail,
                        resolved=check.ok,
                    )
                )
            except Exception:
                logger.error(
                    "Notification de santé échouée component=%s (retenté au prochain cycle)",
                    check.component,
                )
                continue  # l'état n'est PAS mis à jour : la transition retentera
            notified.add(check.component)
            changed.append(check)
            logger.info(
                "Santé : %s -> %s (%s)",
                check.component,
                "OK" if check.ok else "KO",
                check.detail,
            )
        # Mise à jour des états : toutes les observations au premier cycle,
        # ensuite uniquement les transitions réellement notifiées.
        for check in checks:
            if self._cycle == 0 or check.component in notified:
                self._states[check.component] = check.ok
        self._cycle += 1
        return changed

    async def run(self) -> None:
        logger.info(
            "Contrôles de santé toutes les %d s (silence moteur max %d s)",
            self._interval,
            int(self._max_silence.total_seconds()),
        )
        while True:
            try:
                await self.check_once()
            except Exception:
                # Un échec inattendu ne doit jamais arrêter la surveillance.
                logger.exception("Cycle de contrôle de santé échoué (repris au prochain cycle)")
            await asyncio.sleep(self._interval)
