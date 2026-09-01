"""Service d'envoi Discord (Phase 12).

Encapsule totalement discord.py : le pipeline des signaux ne manipule jamais
d'objets Discord directement, il dépend de cette interface injectable (et
donc mockable dans les tests).

Invariants :
- l'envoi ne doit jamais lever une exception non gérée vers le pipeline :
  toute défaillance devient DiscordSendError ;
- le message_id retourné est stocké en base (traçabilité du statut SENT) ;
- le token/les identifiants ne sont jamais loggés.
"""

import logging
from typing import Protocol

import discord

logger = logging.getLogger(__name__)


class DiscordSendError(Exception):
    """Échec d'envoi vers Discord (indisponibilité, salon introuvable...)."""


class SignalNotifier(Protocol):
    """Interface attendue par le pipeline (structure pour les tests/fakes)."""

    async def send_signal(self, embed: discord.Embed) -> int:
        """Envoie l'embed et retourne l'identifiant du message."""
        ...


class DiscordService:
    """Envoi réel via le bot discord.py démarré dans le lifespan."""

    def __init__(self, bot: discord.Client, channel_id: int | None):
        self._bot = bot
        self._channel_id = channel_id

    async def send_signal(self, embed: discord.Embed) -> int:
        """Publie l'embed dans le salon des signaux, retourne message_id."""
        if self._channel_id is None:
            raise DiscordSendError("salon des signaux non configuré")
        if not self._bot.is_ready():
            raise DiscordSendError("bot Discord non connecté")
        try:
            channel = self._bot.get_channel(self._channel_id)
            if channel is None:
                channel = await self._bot.fetch_channel(self._channel_id)
            message = await channel.send(embed=embed)  # type: ignore[union-attr]
        except DiscordSendError:
            raise
        except Exception as exc:
            logger.error("Envoi Discord échoué : %s", type(exc).__name__)
            raise DiscordSendError(str(exc)) from exc
        logger.info("Discord notification sent message_id=%s", message.id)
        return message.id


# --- Instances courantes, branchées dans le lifespan de app.main ---

_notifier: SignalNotifier | None = None
# Notifieur du salon récap quotidien (clôtures TP/SL, résumé de 22h) : peut
# rester None (salon non configuré) — les notifications sont alors ignorées.
_recap_notifier: SignalNotifier | None = None


def set_notifier(notifier: SignalNotifier | None) -> None:
    """Enregistre le notifieur (tests : fake ; production : DiscordService)."""
    global _notifier
    _notifier = notifier


def provide_notifier() -> SignalNotifier:
    """Dépendance FastAPI : notifieur Discord courant (surchargeable en test)."""
    if _notifier is None:
        raise RuntimeError("Notifieur Discord non initialisé")
    return _notifier


def set_recap_notifier(notifier: SignalNotifier | None) -> None:
    """Enregistre le notifieur du salon récap (None = désactivé)."""
    global _recap_notifier
    _recap_notifier = notifier


def provide_recap_notifier() -> SignalNotifier | None:
    """Dépendance FastAPI : notifieur du salon récap (None si non configuré)."""
    return _recap_notifier


async def notify_closure(outcome, notifier: SignalNotifier | None) -> None:
    """Publie l'embed de clôture d'une position paper dans le salon récap.

    Best-effort : un échec d'envoi est loggé et n'affecte jamais le pipeline
    (la clôture reste enregistrée en base).
    """
    if notifier is None:
        return
    from app.discord.embeds import build_closure_embed

    try:
        await notifier.send_signal(build_closure_embed(outcome))
    except DiscordSendError:
        logger.error(
            "Notification de clôture échouée position_id=%s symbol=%s (ignoré)",
            outcome.position_id,
            outcome.symbol,
        )
