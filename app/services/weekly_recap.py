"""Récap hebdomadaire du paper trading (vendredi 22h, heure locale configurée).

Chaque vendredi à l'heure configurée (Europe/Paris par défaut), publie dans le
salon Discord dédié un récap simplifié (format validé 2026-09-11) :
- la performance de la semaine (trades, gagnants/perdants, winrate, total R,
  PF, moyenne) ;
- les ventilations par symbole et par direction ;
- le meilleur et le pire trade ;
- TOUTES les positions encore en cours (une ligne chacune, jusqu'à TP/SL).

Lecture seule de la base + envoi Discord : aucune décision, aucun ordre
(règle absolue du projet — simulation locale uniquement).
"""

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.repository import PaperRepository
from app.discord.embeds import WEEKDAY_LABELS, build_weekly_recap_embed
from app.services.discord_service import SignalNotifier

logger = logging.getLogger(__name__)

# Le sommeil est découpé (1 h max) : robuste aux changements d'heure système
# et aux mises en pause du conteneur, l'échéance étant recalculée à chaque pas.
_MAX_SLEEP_SECONDS = 3600


def compute_next_run(
    now_utc: datetime, hour: int, weekday: int, tz: ZoneInfo
) -> datetime:
    """Prochaine occurrence de `weekday` à `hour` heure locale, après now.

    Retourne un datetime UTC conscient ; gère l'heure d'été/hiver.
    """
    now_local = now_utc.astimezone(tz)
    candidate = datetime.combine(now_local.date(), time(hour, 0), tzinfo=tz)
    if candidate <= now_local:
        candidate += timedelta(days=1)
    candidate += timedelta(days=(weekday - candidate.weekday()) % 7)
    return candidate.astimezone(timezone.utc)


class WeeklyRecapService:
    """Boucle d'envoi du récap hebdomadaire (démarrée dans le lifespan)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        notifier: SignalNotifier,
        *,
        hour: int = 22,
        weekday: int = 4,  # vendredi
        timezone_name: str = "Europe/Paris",
    ):
        self._session_factory = session_factory
        self._notifier = notifier
        self._hour = hour
        self._weekday = weekday
        self._tz = ZoneInfo(timezone_name)

    async def post_recap(self, now_utc: datetime | None = None) -> int:
        """Construit et publie le récap de la semaine ; retourne message_id."""
        now = now_utc or datetime.now(timezone.utc)
        now_local = now.astimezone(self._tz)
        week_start = now - timedelta(days=7)

        async with self._session_factory() as session:
            repository = PaperRepository(session)
            cloturees = await repository.closed_between(week_start, now)
            en_cours = await repository.open_all()

        embed = build_weekly_recap_embed(
            debut=week_start.astimezone(self._tz),
            fin=now_local,
            cloturees_semaine=cloturees,
            en_cours=en_cours,
        )
        message_id = await self._notifier.send_signal(embed)
        logger.info(
            "Récap hebdomadaire publié semaine=%s->%s clôturées=%d en_cours=%d message_id=%s",
            week_start.date(),
            now_local.date(),
            len(cloturees),
            len(en_cours),
            message_id,
        )
        return message_id

    async def run(self) -> None:
        logger.info(
            "Récap hebdomadaire programmé chaque %s à %02dh00 (%s)",
            WEEKDAY_LABELS[self._weekday],
            self._hour,
            self._tz.key,
        )
        while True:
            target = compute_next_run(
                datetime.now(timezone.utc), self._hour, self._weekday, self._tz
            )
            while True:
                remaining = (target - datetime.now(timezone.utc)).total_seconds()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, _MAX_SLEEP_SECONDS))
            try:
                await self.post_recap()
            except Exception:
                # Un échec (Discord indisponible, base injoignable) ne doit
                # jamais arrêter la boucle : le récap repartira la semaine
                # suivante.
                logger.exception("Échec du récap hebdomadaire (repris au prochain cycle)")
