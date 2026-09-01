"""Envoi des signaux vers le webhook du backend bot-td.

Le JSON est identique à celui d'une alerte TradingView : le backend ne fait
aucune différence. Retry avec backoff sur erreur réseau / 5xx ; un 401
(secret invalide) n'est pas retenté. Le secret ne figure jamais dans les logs.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0


class WebhookError(Exception):
    """Échec définitif de l'envoi du signal."""


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    context: str,
) -> dict:
    """POST JSON avec retry (erreur réseau / 5xx) ; 401 = échec définitif."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.post(url, json=payload, timeout=10.0)
        except httpx.HTTPError as exc:
            logger.warning(
                "Webhook injoignable (%s) tentative %d/%d : %s",
                type(exc).__name__,
                attempt,
                MAX_ATTEMPTS,
                exc,
            )
            if attempt == MAX_ATTEMPTS:
                raise WebhookError("webhook injoignable") from exc
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue

        if response.status_code == 200:
            body = response.json()
            logger.info("Webhook 200 %s réponse=%s", context, body)
            return body
        if response.status_code == 401:
            # Secret invalide : retenter ne servirait à rien.
            logger.error("Webhook 401 (secret invalide) %s", context)
            raise WebhookError("webhook rejeté : secret invalide (401)")
        if 500 <= response.status_code:
            logger.warning(
                "Webhook %d tentative %d/%d %s",
                response.status_code,
                attempt,
                MAX_ATTEMPTS,
                context,
            )
            if attempt == MAX_ATTEMPTS:
                raise WebhookError(f"webhook en échec : statut {response.status_code}")
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue
        # Autre statut (4xx) : échec définitif sans retry.
        logger.error("Webhook statut inattendu %d %s", response.status_code, context)
        raise WebhookError(f"webhook en échec : statut {response.status_code}")

    raise WebhookError("webhook en échec")  # inatteignable, garde-fou typage


async def send_signal(
    client: httpx.AsyncClient,
    webhook_url: str,
    payload: dict,
) -> dict:
    """POST le signal ; retourne la réponse JSON du backend.

    Réponses 200 attendues : {"status": "sent"}, "duplicate", "rejected"...
    (le backend répond 200 même pour un rejet métier — comportement normal).
    """
    context = (
        f"strategy={payload.get('strategy')} symbol={payload.get('symbol')} "
        f"timeframe={payload.get('timeframe')} action={payload.get('action')}"
    )
    return await _post_json(client, webhook_url, payload, context)


async def send_price_update(
    client: httpx.AsyncClient,
    price_url: str,
    payload: dict,
) -> dict:
    """POST une bougie fermée vers POST /internal/prices du backend.

    Même mécanique de retry que les signaux ; le secret ne figure jamais
    dans les logs.
    """
    context = (
        f"price-update symbol={payload.get('symbol')} "
        f"timeframe={payload.get('timeframe')} open_time={payload.get('open_time')}"
    )
    return await _post_json(client, price_url, payload, context)
