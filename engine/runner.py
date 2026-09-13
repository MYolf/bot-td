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

from engine.binance_client import fetch_closed_candles
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
from engine.webhook_client import send_price_update, send_signal

logger = logging.getLogger(__name__)

# Signature des dépendances injectables (tests) :
Fetcher = Callable[[str, str, int], Awaitable[list[Candle]]]
Sender = Callable[[dict], Awaitable[dict]]


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
    ) -> None:
        self._settings = settings
        self._fetcher = fetcher
        self._sender = sender
        self._params = params or MomentumParams()
        self._price_sender = price_sender
        self._macro = macro
        self._last_open_time: dict[str, int] = {}
        self._trackers: dict[str, PositionTracker] = {}

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
                continue  # aucune nouvelle bougie fermée

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
            if result is None:
                continue
            # 3bis) Filtre qualité (ENGINE_MIN_SCORE) : une transition filtrée
            # n'est ni émise ni ouverte en simulation — un signal postérieur
            # de meilleure qualité dans le même sens restera émissible.
            score = total_score(result)
            if score < self._settings.engine_min_score:
                logger.info(
                    "Transition %s filtrée symbol=%s score=%d < %d (ENGINE_MIN_SCORE)",
                    result.action,
                    symbol,
                    score,
                    self._settings.engine_min_score,
                )
                continue
            # 4) Fidélité TradingView : n'émettre que si un ordre simulé
            #    s'exécuterait (plat ou renversement ; pyramiding = 0).
            if not tracker.would_fill(result.action):
                logger.info(
                    "Transition %s ignorée symbol=%s (position %s déjà ouverte, "
                    "pyramiding=0 côté TradingView)",
                    result.action,
                    symbol,
                    tracker.position.side if tracker.position else "aucune",
                )
                continue
            tracker.open(result.action, result.entry, result.stop_loss, result.take_profit)
            await self._emit(symbol, result)

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
            settings, fetcher, sender, price_sender=price_sender, macro=macro
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
