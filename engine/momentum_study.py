"""Audit de momentum_v1 (stratégie en production) sur long historique.

Questions posées (avant toute décision de modification de la production) :
1. momentum_v1 est-elle réellement négative ? Par année (régime), en brut
   (frais = 0), taker et maker — pour séparer « pas d'edge » de « edge mangé
   par les frais » (leçon Phase 31).
2. Le score total (tendance + momentum + MACD, max 55 en pratique) sépare-t-il
   les bons des mauvais trades ? Par bucket de score, les DEUX symboles.
3. BUY vs SELL : le SELL est-il toxique comme dans toutes les études
   précédentes ?

Variantes (--min-score, --side) : évaluées à titre comparatif, jamais
directement mises en production (protocole IS/OOS + walk-forward d'abord).

Simulation : simulateur commun de confluence_backtest (entrée à clôture,
pyramiding 0, renversement, SL prioritaire, frais en R).

Usage :
    python -m engine.momentum_study --symbol BTCUSDT --days 1490
    python -m engine.momentum_study --symbol ETHUSDT --days 1490 --side buy
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone

import httpx

from engine.binance_client import INTERVAL_MS
from engine.confluence_backtest import (
    SignalEntry,
    Trade,
    compute_metrics,
    simulate_trades,
)
from engine.strategy import (
    Candle,
    MomentumParams,
    SignalResult,
    compute_series,
    evaluate_at,
)
from engine.validation import MAKER_FEE_RT, load_history

FEE_TAKER_RT = 0.0012


def all_transitions(
    candles: list[Candle], params: MomentumParams | None = None
) -> list[SignalResult]:
    """Toutes les transitions de momentum_v1 en un seul passage (O(n)).

    Contrairement à ``engine.backtest.replay`` (O(n²), par préfixe), les
    indicateurs sont calculés une fois ; la parité exacte avec
    ``evaluate_momentum_v1`` est garantie par test. Aucune gestion de
    position ici : c'est le simulateur qui gère (pyramiding 0, renversement).
    """
    params = params or MomentumParams()
    closes = [c.close for c in candles]
    series = compute_series(closes, params)
    min_len = params.ema_slow + params.macd_signal + 2
    out: list[SignalResult] = []
    for i in range(min_len - 1, len(candles)):
        result = evaluate_at(candles, series, i, params)
        if result is not None:
            out.append(result)
    return out


def total_score(result: SignalResult) -> int:
    """Score total du signal (max théorique 55 : momentum_v1 n'évalue que
    tendance /20 + momentum /20 + MACD /15)."""
    return result.score_trend + result.score_momentum + result.score_macd


def to_entries(
    candles: list[Candle],
    transitions: list[SignalResult],
    *,
    min_score: int = 0,
    side: str = "both",
) -> list[SignalEntry]:
    """Transitions -> ordres simulés, avec filtres optionnels.

    Le filtrage a lieu AVANT la simulation : les trades filtrés n'influencent
    plus la gestion de position (c'est la sémantique d'une variante réelle).
    """
    index_by_open = {c.open_time: i for i, c in enumerate(candles)}
    entries: list[SignalEntry] = []
    for result in transitions:
        if total_score(result) < min_score:
            continue
        if side != "both" and result.action.lower() != side:
            continue
        entries.append(
            SignalEntry(
                index=index_by_open[result.candle_open_time],
                action=result.action,
                entry=result.entry,
                stop_loss=result.stop_loss,
                take_profit=result.take_profit,
                score=total_score(result),
            )
        )
    return entries


def group_by_year(candles: list[Candle], trades: list[Trade]) -> dict[int, list[Trade]]:
    """Trades groupés par année civile de leur bougie d'entrée."""
    groups: dict[int, list[Trade]] = defaultdict(list)
    for trade in trades:
        year = datetime.fromtimestamp(
            candles[trade.open_index].open_time / 1000, tz=timezone.utc
        ).year
        groups[year].append(trade)
    return dict(groups)


def _line(label: str, metrics: dict[str, float | int]) -> str:
    if metrics.get("n", 0) == 0:
        return f"{label:<24} aucun trade"
    return (
        f"{label:<24} n={metrics['n']:<4} win={metrics['win_rate']}%  "
        f"exp={metrics['expectancy']}R  PF={metrics['profit_factor']}  "
        f"total={metrics['total_r']}R"
    )


async def _run(args: argparse.Namespace) -> None:
    params = MomentumParams()
    async with httpx.AsyncClient() as client:
        candles = await load_history(client, args.symbol, args.timeframe, args.days)
    if not candles:
        raise SystemExit("aucune bougie récupérée")

    debut = datetime.fromtimestamp(candles[0].open_time / 1000, tz=timezone.utc)
    fin = datetime.fromtimestamp(candles[-1].open_time / 1000, tz=timezone.utc)
    transitions = all_transitions(candles, params)
    print(
        f"=== {args.symbol} {args.timeframe}m | {len(candles)} bougies | "
        f"{debut:%Y-%m-%d} -> {fin:%Y-%m-%d} UTC | {len(transitions)} transitions | "
        f"side={args.side} min_score={args.min_score} ==="
    )

    entries = to_entries(candles, transitions, min_score=args.min_score, side=args.side)
    trades_brut = simulate_trades(candles, entries, 0.0)
    trades_taker = simulate_trades(candles, entries, FEE_TAKER_RT)
    trades_maker = simulate_trades(candles, entries, MAKER_FEE_RT)

    print("\n--- Global ---")
    print(_line("brut (frais 0)", compute_metrics(trades_brut)))
    print(_line(f"taker ({FEE_TAKER_RT:.2%} A/R)", compute_metrics(trades_taker)))
    print(_line(f"maker ({MAKER_FEE_RT:.2%} A/R)", compute_metrics(trades_maker)))

    print("\n--- Par année (taker) ---")
    for year in sorted(group_by_year(candles, trades_taker)):
        trades_annee = group_by_year(candles, trades_taker)[year]
        print(_line(str(year), compute_metrics(trades_annee)))

    print("\n--- Par direction (taker) ---")
    for action in ("BUY", "SELL"):
        print(
            _line(
                action,
                compute_metrics([t for t in trades_taker if t.action == action]),
            )
        )

    print("\n--- Par score total (taker puis brut) ---")
    by_score: dict[int, list[Trade]] = defaultdict(list)
    for trade in trades_taker:
        by_score[trade.score].append(trade)
    for score in sorted(by_score):
        taker_metrics = compute_metrics(by_score[score])
        brut_metrics = compute_metrics(
            [t for t in trades_brut if t.score == score]
        )
        print(
            f"score {score:<3} n={taker_metrics['n']:<4} "
            f"taker exp={taker_metrics['expectancy']}R total={taker_metrics['total_r']}R  "
            f"| brut exp={brut_metrics['expectancy']}R total={brut_metrics['total_r']}R  "
            f"win={taker_metrics['win_rate']}%"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit de momentum_v1 sur long historique (aucun envoi)."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="15", choices=list(INTERVAL_MS))
    parser.add_argument("--days", type=int, default=1490)
    parser.add_argument("--side", choices=("both", "buy", "sell"), default="both")
    parser.add_argument("--min-score", type=int, default=0)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
