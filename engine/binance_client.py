"""Client Binance — données de marché publiques uniquement.

API REST publique /api/v3/klines : aucune clé, aucune authentification,
aucune capacité d'ordre. Conforme à la règle absolue du projet
(signalisation seule).
"""

from __future__ import annotations

import logging
import time

import httpx

from engine.strategy import Candle

logger = logging.getLogger(__name__)

BASE_URL = "https://api.binance.com"

# Timeframes internes (codes backend) -> intervalles Binance.
KLINE_INTERVAL: dict[str, str] = {
    "1": "1m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "60": "1h",
    "240": "4h",
    "D": "1d",
}

# Durée d'une bougie en millisecondes (codes internes).
INTERVAL_MS: dict[str, int] = {
    "1": 60_000,
    "5": 300_000,
    "15": 900_000,
    "30": 1_800_000,
    "60": 3_600_000,
    "240": 14_400_000,
    "D": 86_400_000,
}


class BinanceError(Exception):
    """Erreur de récupération des données Binance."""


async def fetch_candles(
    client: httpx.AsyncClient,
    symbol: str,
    timeframe: str,
    limit: int = 500,
    start_time: int | None = None,
) -> list[Candle]:
    """Récupère les klines (peut inclure la bougie en cours de formation)."""
    interval = KLINE_INTERVAL.get(timeframe)
    if interval is None:
        raise BinanceError(f"Timeframe non supporté : {timeframe}")
    params: dict[str, int | str] = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time is not None:
        params["startTime"] = start_time
    response = await client.get(f"{BASE_URL}/api/v3/klines", params=params)
    if response.status_code != 200:
        # Corps d'erreur potentiellement long : on ne journalise que le code.
        logger.error(
            "Binance klines en échec symbol=%s timeframe=%s status=%d",
            symbol,
            timeframe,
            response.status_code,
        )
        raise BinanceError(f"statut HTTP {response.status_code}")
    return [
        Candle(
            open_time=int(k[0]),
            close_time=int(k[6]),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
        )
        for k in response.json()
    ]


async def fetch_closed_candles(
    client: httpx.AsyncClient,
    symbol: str,
    timeframe: str,
    limit: int = 500,
    start_time: int | None = None,
) -> list[Candle]:
    """Récupère uniquement les bougies FERMÉES (anti-repainting).

    La dernière kline renvoyée par Binance est la bougie en cours de
    formation : elle est écartée ici, une fois pour toutes.
    """
    candles = await fetch_candles(client, symbol, timeframe, limit, start_time)
    now_ms = int(time.time() * 1000)
    return [c for c in candles if c.close_time < now_ms]


async def fetch_forming_candle(
    client: httpx.AsyncClient,
    symbol: str,
    timeframe: str,
) -> Candle | None:
    """Bougie EN COURS de formation (signaux à l'avance), ou None.

    Récupérée telle quelle (high/low provisoires) : elle n'est JAMAIS
    évaluée par momentum_v1 — seule la détection de proximité d'un niveau
    P* (engine/advance.py) la lit, l'état des indicateurs restant calculé
    sur les bougies fermées.
    """
    candles = await fetch_candles(client, symbol, timeframe, limit=2)
    now_ms = int(time.time() * 1000)
    for c in reversed(candles):
        if c.close_time >= now_ms:
            return c
    return None
