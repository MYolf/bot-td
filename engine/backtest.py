"""Vérification du portage : rejoue Momentum V1 sur l'historique Binance.

But : comparer les signaux du moteur Python avec ceux que la stratégie Pine
affiche sur TradingView (mêmes bougies, mêmes triangles), AVANT de mettre
le moteur en production.

Usage :
    python -m engine.backtest --symbol BTCUSDT --days 30
    python -m engine.backtest --symbol BTCUSDT --timeframe 60 --days 90

Aucun envoi de webhook : lecture et affichage uniquement.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

import httpx

from engine.binance_client import INTERVAL_MS, BinanceError, fetch_candles
from engine.position import PositionTracker
from engine.strategy import Candle, MomentumParams, evaluate_momentum_v1

KLINE_PAGE_LIMIT = 1000  # maximum accepté par Binance par requête


def replay(candles: list[Candle], params: MomentumParams | None = None):
    """Rejoue la stratégie bougie par bougie, comme le runner en production.

    Ne retient que les signaux ÉMIS (fidélité TradingView : position simulée,
    pyramiding = 0). Retourne (signaux_émis, transitions_ignorées).
    """
    params = params or MomentumParams()
    tracker = PositionTracker()
    emitted = []
    ignored = 0
    min_len = params.ema_slow + params.macd_signal + 2
    for i in range(min_len, len(candles)):
        tracker.apply_candle(candles[i])
        result = evaluate_momentum_v1(candles[: i + 1], params)
        if result is None:
            continue
        if tracker.would_fill(result.action):
            tracker.open(result.action, result.entry, result.stop_loss, result.take_profit)
            emitted.append(result)
        else:
            ignored += 1
    return emitted, ignored


async def gather_history(
    client: httpx.AsyncClient,
    symbol: str,
    timeframe: str,
    days: int,
) -> list[Candle]:
    """Récupère `days` jours d'historique (pagination des klines)."""
    interval_ms = INTERVAL_MS[timeframe]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - days * 86_400_000
    candles: list[Candle] = []
    cursor = start_ms
    while cursor < now_ms:
        page = await fetch_candles(client, symbol, timeframe, KLINE_PAGE_LIMIT, cursor)
        if not page:
            break
        candles.extend(page)
        cursor = page[-1].open_time + interval_ms
    # Écarter la bougie en cours de formation (cohérence avec le runner).
    return [c for c in candles if c.close_time < now_ms]


def _fmt_time(open_time_ms: int) -> str:
    return (
        datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
        .strftime("%Y-%m-%d %H:%M")
    )


async def _run(args: argparse.Namespace) -> None:
    params = MomentumParams()
    async with httpx.AsyncClient() as client:
        candles = await gather_history(client, args.symbol, args.timeframe, args.days)
    if not candles:
        raise BinanceError("aucune bougie récupérée")
    print(
        f"{args.symbol} {args.timeframe}m — {len(candles)} bougies fermées "
        f"du {_fmt_time(candles[0].open_time)} au {_fmt_time(candles[-1].open_time)} UTC"
    )
    signals, ignored = replay(candles, params)
    buys = sells = 0
    for s in signals:
        if s.action == "BUY":
            buys += 1
        else:
            sells += 1
        print(
            f"{_fmt_time(s.candle_open_time)}  {s.action:<4} "
            f"entry={s.entry:.2f} sl={s.stop_loss:.2f} tp={s.take_profit:.2f} "
            f"scores(t/m/macd)={s.score_trend}/{s.score_momentum}/{s.score_macd}"
        )
    print(
        f"\nTotal : {len(signals)} signaux émis ({buys} BUY, {sells} SELL), "
        f"{ignored} transitions ignorées (position déjà ouverte, comme TradingView)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rejoue Momentum V1 sur l'historique Binance (aucun envoi)."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="15", choices=list(INTERVAL_MS))
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
