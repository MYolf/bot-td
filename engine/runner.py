"""Boucle principale du moteur de signaux.

Chaque cycle :
1. récupérer les bougies FERMÉES de chaque symbole (API publique Binance) ;
2. si une NOUVELLE bougie fermée est apparue, évaluer Momentum V1 sur
   l'historique (la nouvelle bougie devient la dernière bougie fermée) ;
3. POSTer la bougie fermée vers /internal/prices du backend (le paper trading
   clôture les positions au TP/SL sans attendre le signal suivant) ;
4. en cas de transition, POST le signal vers le webhook du backend.

Garanties :
- la bougie en cours de formation n'est JAMAIS évaluée (anti-repainting) ;
- le premier relevé n'émet rien : il reconstruit l'état de la position
  simulée depuis l'historique (moteur sans état persistant) ;
- une même bougie n'est évaluée qu'une fois (état en mémoire) ; et même en
  cas de double émission, la déduplication backend reste le filet de
  sécurité (timestamp = borne de clôture -> signal_uid identique) ;
- fidélité TradingView : un signal dans le sens de la position déjà ouverte
  n'est PAS émis (pyramiding = 0 => pas d'ordre => pas d'alerte) ; seuls
  l'ouverture et le renversement émettent.

Lancement : python -m engine.runner
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from engine.advance import (
    build_advance_payload,
    build_anticipative_payload,
    build_confirmed_payload,
    build_expiration_payload,
    build_invalidation_payload,
    plan_advance,
    plan_anticipative,
)
from engine.binance_client import fetch_closed_candles, fetch_forming_candle
from engine.config import EngineSettings, get_engine_settings
from engine.macro.calendar import load_calendar
from engine.macro.models import MacroLevel
from engine.macro.risk_engine import MacroGate
from engine.position import PositionTracker, replay_history
from engine.strategy import (
    Candle,
    MomentumParams,
    SignalResult,
    build_payload,
    evaluate_momentum_v1,
    total_score,
)
from engine.webhook_client import send_advance_alert, send_price_update, send_signal

logger = logging.getLogger(__name__)

# Signature des dépendances injectables (tests) :
Fetcher = Callable[[str, str, int], Awaitable[list[Candle]]]
Sender = Callable[[dict], Awaitable[dict]]
# Bougie en formation (signaux à l'avance) : None si pas de bougie ouverte.
AdvanceFetcher = Callable[[str, str], Awaitable[Candle | None]]


class SignalEngine:
    """Moteur : état minimal (dernière bougie vue par symbole) + logique."""

    def __init__(
        self,
        settings: EngineSettings,
        fetcher: Fetcher,
        sender: Sender,
        params: MomentumParams | None = None,
        price_sender: Sender | None = None,
        macro: MacroGate | None = None,
        advance_sender: Sender | None = None,
        forming_fetcher: AdvanceFetcher | None = None,
    ) -> None:
        self._settings = settings
        self._fetcher = fetcher
        self._sender = sender
        self._params = params or MomentumParams()
        self._price_sender = price_sender
        self._macro = macro
        self._advance_sender = advance_sender
        self._forming_fetcher = forming_fetcher
        self._last_open_time: dict[str, int] = {}
        self._trackers: dict[str, PositionTracker] = {}
        # Signaux à l'avance : pré-alerte en attente de résolution par symbole
        # {open_time de la bougie annoncée, action, niveau} ; et dernière
        # émission officielle (open_time, action) pour distinguer « confirmé
        # par le signal réel » de « touché mais non confirmé ».
        self._advance_pending: dict[str, dict] = {}
        self._advance_emitted: dict[str, tuple[int, str]] = {}
        # Mode anticipatif (ANTICIPATION.md) : une annonce active par
        # (symbole, direction), valable horizon bougies ; et mémoire des
        # émissions officielles récentes (open_time, action) pour la
        # résolution — confirmé / annulé / expiré / invalidé.
        self._antic_pending: dict[tuple[str, str], dict] = {}
        self._emissions_seen: dict[str, set[tuple[int, str]]] = {}

    async def poll_once(self) -> None:
        """Un cycle complet sur tous les symboles configurés."""
        for symbol in self._settings.engine_symbols:
            try:
                candles = await self._fetcher(
                    symbol, self._settings.engine_timeframe, self._settings.engine_candle_limit
                )
            except Exception:
                logger.exception(
                    "Récupération des bougies échouée symbol=%s (cycle ignoré)", symbol
                )
                continue
            if not candles:
                logger.warning("Aucune bougie reçue symbol=%s", symbol)
                continue

            last = candles[-1]
            previous_open = self._last_open_time.get(symbol)
            if previous_open == last.open_time:
                # Pas de nouvelle bougie fermée : seule la surveillance « à
                # l'avance » travaille (elle lit la bougie en formation,
                # donc à chaque cycle, pas seulement aux clôtures).
                await self._advance_cycle(symbol, candles)
                continue

            self._last_open_time[symbol] = last.open_time
            if previous_open is None:
                # Premier relevé depuis le démarrage : on reconstruit l'état de
                # la position simulée depuis l'historique (sans rien émettre),
                # puis on mémorise la dernière bougie fermée. Le filtre qualité
                # (ENGINE_MIN_SCORE) s'applique aussi ici : une transition
                # historique filtrée n'ouvre pas de position simulée.
                self._trackers[symbol] = replay_history(
                    candles, self._params, min_score=self._settings.engine_min_score
                )
                logger.info(
                    "Premier relevé symbol=%s : %d bougies fermées, position simulée=%s",
                    symbol,
                    len(candles),
                    self._trackers[symbol].position,
                )
                await self._advance_cycle(symbol, candles)
                continue

            tracker = self._trackers[symbol]
            # 1) La nouvelle bougie est envoyée au backend : le paper trading
            #    peut clôturer ses positions au TP/SL dès maintenant (best-effort).
            await self._send_price(symbol, last)
            # 2) La nouvelle bougie peut refermer la position simulée (SL/TP).
            exit_reason = tracker.apply_candle(last)
            if exit_reason is not None:
                logger.info(
                    "Position simulée close symbol=%s raison=%s bougie=%s",
                    symbol,
                    exit_reason,
                    last.open_time,
                )

            # 3) Transition sur la nouvelle bougie fermée ?
            result = evaluate_momentum_v1(candles, self._params)
            if result is not None:
                # 3bis) Filtre qualité (ENGINE_MIN_SCORE) : une transition
                # filtrée n'est ni émise ni ouverte en simulation — un signal
                # postérieur de meilleure qualité dans le même sens restera
                # émissible.
                score = total_score(result)
                if score < self._settings.engine_min_score:
                    logger.info(
                        "Transition %s filtrée symbol=%s score=%d < %d (ENGINE_MIN_SCORE)",
                        result.action,
                        symbol,
                        score,
                        self._settings.engine_min_score,
                    )
                # 4) Fidélité TradingView : n'émettre que si un ordre simulé
                #    s'exécuterait (plat ou renversement ; pyramiding = 0).
                elif not tracker.would_fill(result.action):
                    logger.info(
                        "Transition %s ignorée symbol=%s (position %s déjà ouverte, "
                        "pyramiding=0 côté TradingView)",
                        result.action,
                        symbol,
                        tracker.position.side if tracker.position else "aucune",
                    )
                else:
                    tracker.open(
                        result.action, result.entry, result.stop_loss, result.take_profit
                    )
                    await self._emit(symbol, result)
                    # Mémorisé APRÈS un envoi réussi uniquement : c'est ce que
                    # la résolution des pré-alertes compare (signal officiel vu
                    # par le backend == pré-alerte confirmée).
                    self._advance_emitted[symbol] = (
                        result.candle_open_time,
                        result.action,
                    )
                    # Mémoire des émissions récentes (résolution du mode
                    # anticipatif) — bornée pour ne jamais croître.
                    seen = self._emissions_seen.setdefault(symbol, set())
                    seen.add((result.candle_open_time, result.action))
                    if len(seen) > 64:
                        keep = sorted(seen)[-32:]
                        seen.clear()
                        seen.update(keep)
            # 5) Signaux à l'avance : résoudre la pré-alerte éventuelle de la
            #    bougie qui vient de fermer, puis sonder la bougie en formation.
            await self._advance_cycle(symbol, candles)

    async def _advance_cycle(self, symbol: str, closed: list[Candle]) -> None:
        """Dispatch signaux à l'avance selon ENGINE_ADVANCE_MODE (spec
        ANTICIPATION.md §8) : reactive (comportement historique) ou
        anticipative (annonce à l'ouverture, validité N bougies)."""
        if self._settings.engine_advance_mode == "anticipative":
            await self._resolve_anticipative(symbol, closed)
            await self._announce_anticipative(symbol, closed)
        else:
            await self._resolve_advance(symbol, closed)
            await self._announce_advance(symbol, closed)

    async def _announce_advance(self, symbol: str, closed: list[Candle]) -> None:
        """Surveille la bougie EN FORMATION : annonce le niveau P* s'il approche.

        Best-effort et purement informatif : aucune incidence sur le pipeline
        officiel (pas de position simulée, pas de signal_uid). Une seule
        annonce par bougie en formation ; l'état des indicateurs est pris
        sur les bougies fermées (anti-repainting).
        """
        if self._advance_sender is None or self._forming_fetcher is None:
            return
        tracker = self._trackers.get(symbol)
        if tracker is None:
            return  # premier relevé pas encore fait : état inconnu
        try:
            forming = await self._forming_fetcher(
                symbol, self._settings.engine_timeframe
            )
        except Exception:
            logger.exception(
                "Récupération de la bougie en formation échouée symbol=%s (ignoré)",
                symbol,
            )
            return
        if forming is None:
            return
        last_closed_open = self._last_open_time.get(symbol)
        if last_closed_open is None or forming.open_time <= last_closed_open:
            return  # bougie incohérente avec l'état du moteur
        pending = self._advance_pending.get(symbol)
        if pending is not None and pending["open_time"] == forming.open_time:
            return  # déjà annoncée pour cette bougie
        plan = plan_advance(
            closed,
            forming,
            self._params,
            min_score=self._settings.engine_min_score,
            eps=self._settings.engine_advance_eps,
        )
        if plan is None:
            return
        if not tracker.would_fill(plan.action):
            # Fidélité pyramiding=0 : aucun signal officiel ne serait émis
            # dans ce sens (position déjà ouverte) — l'annonce serait du bruit.
            return
        payload = build_advance_payload(
            plan,
            symbol=symbol,
            timeframe=self._settings.engine_timeframe,
            secret=self._settings.tradingview_webhook_secret,
        )
        try:
            await self._advance_sender(payload)
        except Exception:
            logger.exception(
                "Envoi pré-alerte échoué symbol=%s action=%s (ignoré, sans "
                "conséquence : le signal officiel restera émis à la clôture)",
                symbol,
                plan.action,
            )
            return
        self._advance_pending[symbol] = {
            "open_time": forming.open_time,
            "action": plan.action,
            "level": plan.level,
        }
        logger.info(
            "Pré-alerte envoyée symbol=%s action=%s niveau=%.8f bougie=%s",
            symbol,
            plan.action,
            plan.level,
            forming.open_time,
        )

    async def _resolve_advance(self, symbol: str, closed: list[Candle]) -> None:
        """Clôture le cycle d'une pré-alerte quand sa bougie est fermée.

        - signal officiel émis pour cette bougie et ce sens -> confirmé,
          silence (l'embed officiel a déjà tout dit) ;
        - niveau touché sans signal officiel -> annulation envoyée
          (l'ordre limite de l'utilisateur a pu être rempli : il faut
          décharger) ;
        - niveau jamais touché -> silence (ordre limite jamais exécuté).
        """
        pending = self._advance_pending.get(symbol)
        if pending is None:
            return
        candle = next(
            (c for c in closed if c.open_time == pending["open_time"]), None
        )
        if candle is None:
            return  # la bougie annoncée est encore en formation
        del self._advance_pending[symbol]
        if self._advance_emitted.get(symbol) == (
            candle.open_time,
            pending["action"],
        ):
            logger.info(
                "Pré-alerte confirmée symbol=%s action=%s bougie=%s (signal "
                "officiel émis, pas d'annulation)",
                symbol,
                pending["action"],
                candle.open_time,
            )
            return
        touched = (
            candle.high >= pending["level"]
            if pending["action"] == "BUY"
            else candle.low <= pending["level"]
        )
        if not touched:
            logger.info(
                "Pré-alerte non touchée symbol=%s action=%s bougie=%s (silence)",
                symbol,
                pending["action"],
                candle.open_time,
            )
            return
        if self._advance_sender is None:
            return
        payload = build_invalidation_payload(
            symbol=symbol,
            timeframe=self._settings.engine_timeframe,
            secret=self._settings.tradingview_webhook_secret,
            action=pending["action"],
            level=pending["level"],
        )
        try:
            await self._advance_sender(payload)
        except Exception:
            logger.exception(
                "Envoi d'annulation de pré-alerte échoué symbol=%s bougie=%s "
                "(ignoré — rappeler manuellement de décharger la position)",
                symbol,
                candle.open_time,
            )
            return
        logger.info(
            "Pré-alerte annulée symbol=%s action=%s niveau=%.8f bougie=%s "
            "(touché non confirmé à la clôture)",
            symbol,
            pending["action"],
            pending["level"],
            candle.open_time,
        )

    # ------------------------------------- mode anticipatif (ANTICIPATION.md) --

    async def _announce_anticipative(self, symbol: str, closed: list[Candle]) -> None:
        """Annonce le niveau P* DÈS L'OUVERTURE de la bougie en formation.

        Anti-spam (spec §3) : une seule annonce active par direction ; le
        niveau annoncé ne dérive pas — pas de re-annonce tant que l'annonce
        active n'est pas résolue (confirmée / annulée / expirée / invalidée).
        Best-effort, aucune incidence sur le pipeline officiel.
        """
        if self._advance_sender is None or self._forming_fetcher is None:
            return
        tracker = self._trackers.get(symbol)
        if tracker is None:
            return  # premier relevé pas encore fait : état inconnu
        try:
            forming = await self._forming_fetcher(
                symbol, self._settings.engine_timeframe
            )
        except Exception:
            logger.exception(
                "Récupération de la bougie en formation échouée symbol=%s (ignoré)",
                symbol,
            )
            return
        if forming is None:
            return
        last_closed_open = self._last_open_time.get(symbol)
        if last_closed_open is None or forming.open_time <= last_closed_open:
            return  # bougie incohérente avec l'état du moteur
        plan = plan_anticipative(
            closed,
            forming,
            self._params,
            min_score=self._settings.engine_min_score,
            k_atr=self._settings.engine_advance_k_atr,
        )
        if plan is None:
            return
        if (symbol, plan.action) in self._antic_pending:
            return  # une annonce est déjà active dans cette direction
        if not tracker.would_fill(plan.action):
            # Fidélité pyramiding=0 : aucun signal officiel ne serait émis
            # dans ce sens (position déjà ouverte) — l'annonce serait du bruit.
            return
        payload = build_anticipative_payload(
            plan,
            symbol=symbol,
            timeframe=self._settings.engine_timeframe,
            secret=self._settings.tradingview_webhook_secret,
            horizon=self._settings.engine_advance_horizon,
        )
        try:
            await self._advance_sender(payload)
        except Exception:
            logger.exception(
                "Envoi pré-alerte anticipative échoué symbol=%s action=%s (ignoré, "
                "sans conséquence : le signal officiel restera émis à la clôture)",
                symbol,
                plan.action,
            )
            return
        self._antic_pending[(symbol, plan.action)] = {
            "open_time": forming.open_time,
            "action": plan.action,
            "level": plan.level,
        }
        logger.info(
            "Pré-alerte anticipative envoyée symbol=%s action=%s niveau=%.8f "
            "bougie=%s horizon=%d",
            symbol,
            plan.action,
            plan.level,
            forming.open_time,
            self._settings.engine_advance_horizon,
        )

    async def _resolve_anticipative(self, symbol: str, closed: list[Candle]) -> None:
        """Résout chaque annonce anticipative active (spec §3, sémantique
        exacte de l'étude reach_study) :

        - bougie touchée + signal officiel même sens à sa clôture -> message
          « signal validé » (amendement v1.1) ;
        - bougie touchée sans signal officiel -> annulation (décharger) ;
        - signal officiel même sens SANS touche -> silence (consommée,
          l'ordre limite n'a pas été rempli) ;
        - signal officiel opposé avant toute touche -> annulation (scénario
          invalidé) ;
        - horizon écoulé sans touche -> expiration (retirer l'ordre limite).
        """
        horizon = self._settings.engine_advance_horizon
        seen = self._emissions_seen.get(symbol, set())
        for key in [
            k for k in self._antic_pending if k[0] == symbol
        ]:
            pending = self._antic_pending[key]
            action = pending["action"]
            opposite = "SELL" if action == "BUY" else "BUY"
            window = [c for c in closed if c.open_time >= pending["open_time"]]
            resolved = False
            for offset, candle in enumerate(window[:horizon]):
                touched = (
                    candle.high >= pending["level"]
                    if action == "BUY"
                    else candle.low <= pending["level"]
                )
                emitted_same = (candle.open_time, action) in seen
                emitted_opposite = (candle.open_time, opposite) in seen
                if touched:
                    await self._send_anticipative_resolution(
                        symbol,
                        pending,
                        kind="confirmed" if emitted_same else "invalidated",
                    )
                    resolved = True
                    break
                if emitted_same:
                    logger.info(
                        "Pré-alerte anticipative consommée symbol=%s action=%s "
                        "bougie=%s (signal officiel sans touche : silence)",
                        symbol,
                        action,
                        candle.open_time,
                    )
                    resolved = True
                    break
                if emitted_opposite:
                    await self._send_anticipative_resolution(
                        symbol, pending, kind="invalidated"
                    )
                    resolved = True
                    break
            if resolved:
                del self._antic_pending[key]
                continue
            if len(window) >= horizon:
                # Horizon écoulé sans touche : expiration.
                await self._send_anticipative_resolution(
                    symbol, pending, kind="expired"
                )
                del self._antic_pending[key]

    async def _send_anticipative_resolution(
        self, symbol: str, pending: dict, kind: str
    ) -> None:
        if self._advance_sender is None:
            return
        builder = {
            "confirmed": build_confirmed_payload,
            "expired": build_expiration_payload,
            "invalidated": build_invalidation_payload,
        }[kind]
        payload = builder(
            symbol=symbol,
            timeframe=self._settings.engine_timeframe,
            secret=self._settings.tradingview_webhook_secret,
            action=pending["action"],
            level=pending["level"],
        )
        try:
            await self._advance_sender(payload)
        except Exception:
            logger.exception(
                "Envoi de résolution de pré-alerte (%s) échoué symbol=%s "
                "bougie=%s (ignoré, best-effort)",
                kind,
                symbol,
                pending["open_time"],
            )
            return
        logger.info(
            "Pré-alerte anticipative résolue (%s) symbol=%s action=%s niveau=%.8f",
            kind,
            symbol,
            pending["action"],
            pending["level"],
        )

    async def _send_price(self, symbol: str, candle: Candle) -> None:
        """POSTe une bougie fermée vers /internal/prices (best-effort).

        Un échec est loggé sans jamais interrompre le cycle : la bougie
        suivante permettra de rattraper la vérification TP/SL.
        """
        if self._price_sender is None:
            return
        payload = {
            "secret": self._settings.tradingview_webhook_secret,
            "symbol": symbol,
            "timeframe": self._settings.engine_timeframe,
            "open_time": candle.open_time,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
        }
        try:
            await self._price_sender(payload)
        except Exception:
            logger.exception(
                "Envoi price update échoué symbol=%s bougie=%s (rattrapé à la bougie suivante)",
                symbol,
                candle.open_time,
            )

    async def _emit(self, symbol: str, result: SignalResult) -> None:
        # Macro display-only (MACRO.md §10) : le gate NE BLOQUE JAMAIS —
        # il annote le payload si un événement suivi est à proximité
        # (HIGH/EXTREME). UNKNOWN (planning indisponible) = pas d'annotation,
        # le signal part normalement.
        macro_level: str | None = None
        macro_note: str | None = None
        if self._macro is not None:
            ts = datetime.fromtimestamp(result.candle_close_time / 1000, tz=timezone.utc)
            context = self._macro.context_at(ts)
            if context.level in (MacroLevel.HIGH, MacroLevel.EXTREME):
                macro_level = context.level.value
                macro_note = context.note
                logger.info(
                    "Contexte macro %s symbol=%s : %s",
                    macro_level,
                    symbol,
                    macro_note,
                )
        payload = build_payload(
            result,
            symbol=symbol,
            timeframe=self._settings.engine_timeframe,
            secret=self._settings.tradingview_webhook_secret,
            macro_level=macro_level,
            macro_note=macro_note,
        )
        try:
            response = await self._sender(payload)
        except Exception:
            logger.exception(
                "Envoi webhook échoué symbol=%s action=%s (le signal sera "
                "perdu pour cette bougie ; la dédup interdit tout simple rejeu)",
                symbol,
                result.action,
            )
            return
        logger.info(
            "Signal émis symbol=%s action=%s entry=%.8f réponse=%s",
            symbol,
            result.action,
            result.entry,
            response,
        )

    async def run(self) -> None:
        logger.info(
            "Moteur démarré symbols=%s timeframe=%s poll=%ds webhook=%s min_score=%d",
            self._settings.engine_symbols,
            self._settings.engine_timeframe,
            self._settings.engine_poll_seconds,
            self._settings.engine_webhook_url,
            self._settings.engine_min_score,
        )
        while True:
            await self.poll_once()
            await asyncio.sleep(self._settings.engine_poll_seconds)


async def _main_async() -> None:
    settings = get_engine_settings()
    if not settings.engine_enabled:
        logging.info("Moteur désactivé (ENGINE_ENABLED=false)")
        return
    async with httpx.AsyncClient() as client:

        async def fetcher(symbol: str, timeframe: str, limit: int) -> list[Candle]:
            return await fetch_closed_candles(client, symbol, timeframe, limit)

        async def sender(payload: dict) -> dict:
            return await send_signal(client, settings.engine_webhook_url, payload)

        price_sender: Sender | None = None
        if settings.engine_price_url:
            price_url = settings.engine_price_url

            async def price_sender_fn(payload: dict) -> dict:  # noqa: F811
                return await send_price_update(client, price_url, payload)

            price_sender = price_sender_fn

        # Signaux à l'avance : la bougie en formation est sondée à chaque
        # cycle ; une pré-alerte part vers /internal/prealert si le prix
        # approche du niveau P* qui confirmerait momentum_v1 à la clôture.
        advance_sender: Sender | None = None
        forming_fetcher: AdvanceFetcher | None = None
        if settings.engine_advance_enabled and settings.engine_advance_url:
            advance_url = settings.engine_advance_url

            async def advance_sender_fn(payload: dict) -> dict:
                return await send_advance_alert(client, advance_url, payload)

            async def forming_fetcher_fn(
                symbol: str, timeframe: str
            ) -> Candle | None:
                return await fetch_forming_candle(client, symbol, timeframe)

            advance_sender = advance_sender_fn
            forming_fetcher = forming_fetcher_fn
            logger.info(
                "Signaux à l'avance activés eps=%.4f url=%s",
                settings.engine_advance_eps,
                advance_url,
            )

        # Macro display-only : planning chargé depuis le fichier versionné
        # (aucun réseau). Fichier absent/invalide -> gate UNKNOWN -> aucune
        # annotation, momentum_v1 continue (failsafe MACRO.md §7).
        macro: MacroGate | None = None
        if settings.macro_enabled:
            macro = load_calendar(
                Path(settings.macro_events_file), frozenset(settings.macro_types)
            )
            logger.info(
                "Macro display-only activé types=%s fichier=%s",
                settings.macro_types,
                settings.macro_events_file,
            )

        engine = SignalEngine(
            settings,
            fetcher,
            sender,
            price_sender=price_sender,
            macro=macro,
            advance_sender=advance_sender,
            forming_fetcher=forming_fetcher,
        )
        await engine.run()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(_main_async())
    except KeyboardInterrupt:
        logging.info("Moteur arrêté")


if __name__ == "__main__":
    main()
