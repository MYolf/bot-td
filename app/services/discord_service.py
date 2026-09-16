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
# Notifieur du salon récap (clôtures TP/SL en direct) : peut rester None
# (salon non configuré) — les notifications sont alors ignorées. Le récap
# hebdomadaire du vendredi est le seul envoi planifié de ce salon.
_recap_notifier: SignalNotifier | None = None
# Notifieur du salon des logs (alertes de santé) : None = salon non configuré.
_health_notifier: SignalNotifier | None = None
# Notifieur du salon BE (rappels break-even) : None = salon non configuré.
_be_notifier: SignalNotifier | None = None
# Notifieurs des salons de clôture : SL (pertes) et TP (gains + sorties
# partielles). None = salon non configuré.
_sl_notifier: SignalNotifier | None = None
_tp_notifier: SignalNotifier | None = None
# Notifieur du salon macro : signaux en contexte HIGH/EXTREME (Macro Risk
# Engine display-only, MACRO.md §10). None = salon non configuré (les
# signaux annotés partent alors dans le salon des signaux habituel).
_macro_notifier: SignalNotifier | None = None
# Notifieur des pré-alertes (signaux à l'avance, engine/advance.py) :
# pré-alerte + annulation « touché non confirmé ». None = aucun envoi.
_advance_notifier: SignalNotifier | None = None


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


def set_health_notifier(notifier: SignalNotifier | None) -> None:
    """Enregistre le notifieur du salon des logs (None = désactivé)."""
    global _health_notifier
    _health_notifier = notifier


def provide_health_notifier() -> SignalNotifier | None:
    """Dépendance : notifieur du salon des logs (None si non configuré)."""
    return _health_notifier


def set_be_notifier(notifier: SignalNotifier | None) -> None:
    """Enregistre le notifieur du salon BE (None = désactivé)."""
    global _be_notifier
    _be_notifier = notifier


def provide_be_notifier() -> SignalNotifier | None:
    """Dépendance : notifieur du salon BE (None si non configuré)."""
    return _be_notifier


def set_sl_notifier(notifier: SignalNotifier | None) -> None:
    """Enregistre le notifieur du salon SL (None = désactivé)."""
    global _sl_notifier
    _sl_notifier = notifier


def provide_sl_notifier() -> SignalNotifier | None:
    """Dépendance : notifieur du salon SL (None si non configuré)."""
    return _sl_notifier


def set_macro_notifier(notifier: SignalNotifier | None) -> None:
    """Enregistre le notifieur du salon macro (None = désactivé)."""
    global _macro_notifier
    _macro_notifier = notifier


def provide_macro_notifier() -> SignalNotifier | None:
    """Dépendance : notifieur du salon macro (None si non configuré)."""
    return _macro_notifier


def set_tp_notifier(notifier: SignalNotifier | None) -> None:
    """Enregistre le notifieur du salon TP (None = désactivé)."""
    global _tp_notifier
    _tp_notifier = notifier


def set_advance_notifier(notifier: SignalNotifier | None) -> None:
    """Enregistre le notifieur des pré-alertes (None = désactivé)."""
    global _advance_notifier
    _advance_notifier = notifier


def provide_advance_notifier() -> SignalNotifier | None:
    """Dépendance : notifieur des pré-alertes (None si non configuré)."""
    return _advance_notifier


def provide_tp_notifier() -> SignalNotifier | None:
    """Dépendance : notifieur du salon TP (None si non configuré)."""
    return _tp_notifier


def closure_notifier(exit_reason: str) -> SignalNotifier | None:
    """Notifieur du salon de clôture selon la raison : SL et BE (sortie
    protégée) -> salon SL, TP -> salon TP (le salon récap ne reçoit que le
    récap hebdo)."""
    return _sl_notifier if exit_reason in ("SL", "BE") else _tp_notifier


async def notify_closure(outcome, notifier: SignalNotifier | None) -> None:
    """Publie l'embed de clôture d'une position paper dans le salon dédié
    (SL/BE ou TP selon la raison de sortie, voir closure_notifier).

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


async def notify_be(alert, notifier: SignalNotifier | None) -> None:
    """Publie le rappel break-even dans le salon dédié (best-effort).

    Un échec d'envoi est loggé : le drapeau be_notified reste positionné en
    base, la gestion BE étant humaine de toute façon.
    """
    if notifier is None:
        return
    from app.discord.embeds import build_be_alert_embed

    try:
        await notifier.send_signal(build_be_alert_embed(alert))
    except DiscordSendError:
        logger.error(
            "Notification BE échouée position_id=%s symbol=%s (ignoré)",
            alert.position_id,
            alert.symbol,
        )


async def notify_tp_progress(alert, notifier: SignalNotifier | None) -> None:
    """Publie le rappel de sortie partielle (TP1/TP2) dans le salon TP.

    Best-effort : un échec d'envoi est loggé, le drapeau reste positionné.
    """
    if notifier is None:
        return
    from app.discord.embeds import build_tp_progress_embed

    try:
        await notifier.send_signal(build_tp_progress_embed(alert))
    except DiscordSendError:
        logger.error(
            "Notification TP%d échouée position_id=%s symbol=%s (ignoré)",
            alert.level,
            alert.position_id,
            alert.symbol,
        )


async def notify_advance(alert, notifier: SignalNotifier | None) -> None:
    """Publie une pré-alerte (ou son annulation) du moteur local.

    ``alert`` est le corps validé de POST /internal/prealert (typage en str
    pour éviter une dépendance api -> services -> embeds ici). Best-effort :
    une pré-alerte perdue ne casse rien — le signal officiel reste émis à la
    clôture de la bougie.
    """
    if notifier is None:
        return
    from app.discord.embeds import (
        build_advance_confirmed_embed,
        build_advance_embed,
        build_advance_expired_embed,
        build_advance_invalidated_embed,
    )

    if alert.kind == "invalidated":
        embed = build_advance_invalidated_embed(
            symbol=alert.symbol,
            timeframe=alert.timeframe,
            action=alert.action,
            level=alert.price,
        )
    elif alert.kind == "confirmed":
        embed = build_advance_confirmed_embed(
            symbol=alert.symbol,
            timeframe=alert.timeframe,
            action=alert.action,
            level=alert.price,
        )
    elif alert.kind == "expired":
        embed = build_advance_expired_embed(
            symbol=alert.symbol,
            timeframe=alert.timeframe,
            action=alert.action,
            level=alert.price,
            expires_in=alert.expires_in,
        )
    else:
        components = {
            field: value
            for field, value in (
                ("score_trend", alert.score_trend),
                ("score_momentum", alert.score_momentum),
                ("score_macd", alert.score_macd),
            )
            if value is not None
        }
        embed = build_advance_embed(
            action=alert.action,
            symbol=alert.symbol,
            strategy=alert.strategy,
            timeframe=alert.timeframe,
            entry_price=alert.price,
            stop_loss=alert.stop_loss,
            take_profit=alert.take_profit,
            risk_reward=alert.risk_reward,
            score=sum(components.values()) if components else None,
            score_components=components or None,
            expires_in=alert.expires_in,
            touch_rate=alert.touch_rate,
        )
    try:
        await notifier.send_signal(embed)
    except DiscordSendError:
        logger.error(
            "Notification de pré-alerte échouée kind=%s symbol=%s (ignoré)",
            alert.kind,
            alert.symbol,
        )
