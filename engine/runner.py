"""Boucle principale du moteur de signaux.

Chaque cycle :
1. récupérer les bougies FERMÉES de chaque symbole (API publique Binance) ;
2. si une NOUVELLE bougie fermée est apparue, évaluer Momentum V1 sur
   l'historique (la nouvelle bougie devient la dernière bougie fermée) ;
3. en cas de transition, POST le signal vers le webhook du backend.

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
from typing import Awaitable, Callable

import httpx

from engine.binance_client import fetch_closed_candles
from engine.config import EngineSettings, get_engine_settings
from engine.position import PositionTracker, replay_history
from engine.strategy import (
    Candle,
    MomentumParams,
    SignalResult,
    build_payload,
    evaluate_momentum_v1,
)
from engine.webhook_client import send_signal

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
    ) -> None:
        self._settings = settings
        self._fetcher = fetcher
        self._sender = sender
        self._params = params or MomentumParams()
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
                # puis on mémorise la dernière bougie fermée.
                self._trackers[symbol] = replay_history(candles, self._params)
                logger.info(
                    "Premier relevé symbol=%s : %d bougies fermées, position simulée=%s",
                    symbol,
                    len(candles),
                    self._trackers[symbol].position,
                )
                continue

            tracker = self._trackers[symbol]
            # 1) La nouvelle bougie peut refermer la position en cours (SL/TP).
            exit_reason = tracker.apply_candle(last)
            if exit_reason is not None:
                logger.info(
                    "Position simulée close symbol=%s raison=%s bougie=%s",
                    symbol,
                    exit_reason,
                    last.open_time,
                )

            # 2) Transition sur la nouvelle bougie fermée ?
            result = evaluate_momentum_v1(candles, self._params)
            if result is None:
                continue
            # 3) Fidélité TradingView : n'émettre que si un ordre simulé
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

    async def _emit(self, symbol: str, result: SignalResult) -> None:
        payload = build_payload(
            result,
            symbol=symbol,
            timeframe=self._settings.engine_timeframe,
            secret=self._settings.tradingview_webhook_secret,
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
            "Moteur démarré symbols=%s timeframe=%s poll=%ds webhook=%s",
            self._settings.engine_symbols,
            self._settings.engine_timeframe,
            self._settings.engine_poll_seconds,
            self._settings.engine_webhook_url,
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

        engine = SignalEngine(settings, fetcher, sender)
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
