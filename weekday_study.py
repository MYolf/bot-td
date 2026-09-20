"""Analyse descriptive : momentum_v1 par jour de la semaine (week-end vs semaine).

Question posée par l'utilisateur (2026-09-20) : les 4 trades réels pris le
week-end sont majoritairement perdants (1 TP / 3 SL). Effet réel ou bruit ?

Lecture seule : bougies 15m Binance (cache data/cache), simulation commune
(engine.confluence_backtest, frais taker), AUCUN changement de production.

Buckets : jour de la semaine de la bougie d'ENTRÉE, en heure de Paris (le
« week-end » de l'utilisateur). Comparaisons : brut vs taker, week-end vs
semaine, week-end par direction (les 4 trades réels week-end sont des LONG).

Usage : python weekday_study.py [--days 1490] [--min-score 45]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8")

import httpx

from engine.confluence_backtest import compute_metrics, simulate_trades
from engine.momentum_study import all_transitions, to_entries
from engine.validation import load_history
from engine.binance_client import INTERVAL_MS

PARIS = ZoneInfo("Europe/Paris")
FEE_TAKER_RT = 0.0012
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _line(label: str, trades: list) -> str:
    metrics = compute_metrics(trades)
    if metrics.get("n", 0) == 0:
        return f"{label:<28} aucun trade"
    return (
        f"{label:<28} n={metrics['n']:<4} win={metrics['win_rate']}%  "
        f"exp={metrics['expectancy']}R  PF={metrics['profit_factor']}  "
        f"total={metrics['total_r']}R"
    )


async def analyse(client: httpx.AsyncClient, symbol: str, days: int, min_score: int) -> None:
    from engine.strategy import MomentumParams

    candles = await load_history(client, symbol, "15", days)
    if not candles:
        raise SystemExit(f"aucune bougie pour {symbol}")
    transitions = all_transitions(candles, MomentumParams())
    entries = to_entries(candles, transitions, min_score=min_score)
    trades = simulate_trades(candles, entries, FEE_TAKER_RT)
    trades_brut = simulate_trades(candles, entries, 0.0)

    debut = datetime.fromtimestamp(candles[0].open_time / 1000, tz=timezone.utc)
    fin = datetime.fromtimestamp(candles[-1].open_time / 1000, tz=timezone.utc)
    print(
        f"\n=== {symbol} 15m | {debut:%Y-%m-%d} -> {fin:%Y-%m-%d} | "
        f"{len(trades)} trades | min_score={min_score} | taker ==="
    )

    # Jour de la semaine de l'entrée, heure de Paris.
    par_jour: dict[int, list] = defaultdict(list)
    brut_par_jour: dict[int, list] = defaultdict(list)
    for trade in trades:
        jour = datetime.fromtimestamp(
            candles[trade.open_index].open_time / 1000, tz=timezone.utc
        ).astimezone(PARIS).weekday()
        par_jour[jour].append(trade)
    for trade in trades_brut:
        jour = datetime.fromtimestamp(
            candles[trade.open_index].open_time / 1000, tz=timezone.utc
        ).astimezone(PARIS).weekday()
        brut_par_jour[jour].append(trade)

    print("\n--- Par jour d'entrée (taker) ---")
    for jour in range(7):
        print(_line(JOURS[jour], par_jour[jour]))

    weekend = par_jour[5] + par_jour[6]
    semaine = [t for j in range(5) for t in par_jour[j]]
    weekend_brut = brut_par_jour[5] + brut_par_jour[6]
    print("\n--- Week-end vs semaine (heure de Paris) ---")
    print(_line("semaine (lun-ven, taker)", semaine))
    print(_line("week-end (sam-dim, taker)", weekend))
    print(_line("week-end (brut)", weekend_brut))
    print("\n--- Week-end par direction (taker) ---")
    for action in ("BUY", "SELL"):
        print(_line(f"week-end {action}", [t for t in weekend if t.action == action]))


async def _run(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient() as client:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            await analyse(client, symbol, args.days, args.min_score)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyse descriptive momentum_v1 par jour de semaine (lecture seule)."
    )
    parser.add_argument("--days", type=int, default=1490)
    parser.add_argument("--min-score", type=int, default=45)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
