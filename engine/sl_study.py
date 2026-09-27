"""Étude « SL élargi » — momentum_v1 avec bracket multiplié (spec SL_ELARGI.md).

Hypothèse : élargir SL/TP d'un facteur k (RR 1:2 conservé) dilue les frais en R
et réduit les sorties secouées par le bruit. Les transitions (conditions
d'entrée) sont identiques pour tous les k : seuls les niveaux de sortie changent.

Grille figée : k ∈ {1, 2, 3, 4} (k=1 = contrôle production). Fenêtres scellées :
IS = début des données → J-550 ; OOS = 550 derniers jours (warmup 20 j),
consommée UNE seule fois pour le k figé à l'issue de l'IS.

Simulation : simulateur commun (entrée à clôture, pyramiding 0, renversement,
SL prioritaire, frais en R). BE et sorties partielles NON simulés (cohérent
avec tous les audits précédents).

Usage :
    python -m engine.sl_study --symbol BTCUSDT --stage is
    python -m engine.sl_study --symbol ETHUSDT --stage oos --mult 3
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

import httpx

from engine.binance_client import INTERVAL_MS
from engine.confluence_backtest import compute_metrics, simulate_trades
from engine.momentum_study import FEE_TAKER_RT, all_transitions, to_entries
from engine.strategy import Candle, MomentumParams
from engine.validation import DAY_MS, MAKER_FEE_RT, load_history, trades_in_window

MULTIPLIERS = (1, 2, 3, 4)
OOS_DAYS = 550
WARMUP_DAYS = 20


def params_for(k: int) -> MomentumParams:
    """Paramètres momentum_v1 avec bracket multiplié par k (RR 1:2 conservé)."""
    return MomentumParams(sl_pct=0.01 * k, tp_pct=0.02 * k)


def split_chrono(
    candles: list[Candle],
    oos_days: int = OOS_DAYS,
    warmup_days: int = WARMUP_DAYS,
) -> tuple[list[Candle], list[Candle], int]:
    """Coupe l'historique en (IS ancienne, OOS récente scellée).

    IS = toutes les bougies strictement avant ``oos_start`` (amorce des
    indicateurs incluse depuis le début des données). OOS = les ``oos_days``
    derniers jours, avec un préfixe de ``warmup_days`` pour l'amorce ;
    ``oos_start_ms`` borne la fenêtre UTILE (les trades du warmup sont exclus
    des métriques par ``trades_in_window``).
    """
    if not candles:
        raise ValueError("historique vide")
    end_ms = candles[-1].close_time + 1
    oos_start = end_ms - oos_days * DAY_MS
    warmup = warmup_days * DAY_MS
    is_part = [c for c in candles if c.close_time < oos_start]
    oos_part = [c for c in candles if c.open_time >= oos_start - warmup]
    if not is_part or not oos_part:
        raise ValueError("fenêtres IS/OOS vides : historique trop court")
    return is_part, oos_part, oos_start


def evaluate_k(
    candles: list[Candle],
    k: int,
    min_score: int,
    fee_rate: float,
    window_start_ms: int | None = None,
):
    """Trades de momentum_v1 avec bracket ×k (warmup exclu si fenêtre donnée)."""
    transitions = all_transitions(candles, params_for(k))
    entries = to_entries(candles, transitions, min_score=min_score)
    trades = simulate_trades(candles, entries, fee_rate)
    if window_start_ms is not None:
        trades = trades_in_window(candles, trades, window_start_ms)
    return trades


def _fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _print_k(
    candles: list[Candle], k: int, min_score: int, fee: float, window_start: int
) -> None:
    label = f"k={k} (SL {k}%, TP {2 * k}%)"
    trades_brut = evaluate_k(candles, k, min_score, 0.0, window_start)
    trades_taker = evaluate_k(candles, k, min_score, fee, window_start)
    trades_maker = evaluate_k(candles, k, min_score, MAKER_FEE_RT, window_start)
    for label_fee, metrics in (
        ("brut", compute_metrics(trades_brut)),
        (f"taker {fee:.2%}", compute_metrics(trades_taker)),
        (f"maker {MAKER_FEE_RT:.2%}", compute_metrics(trades_maker)),
    ):
        if metrics.get("n", 0) == 0:
            print(f"{label:<18} {label_fee:<12} aucun trade")
            continue
        print(
            f"{label:<18} {label_fee:<12} n={metrics['n']:<4} "
            f"win={metrics['win_rate']}%  exp={metrics['expectancy']}R  "
            f"PF={metrics['profit_factor']}  total={metrics['total_r']}R  "
            f"DD={metrics['max_drawdown']}R"
        )
    for action in ("BUY", "SELL"):
        metrics = compute_metrics(
            [t for t in trades_taker if t.action == action]
        )
        if metrics.get("n", 0) == 0:
            print(f"{label:<18} {action:<12} aucun trade")
        else:
            print(
                f"{label:<18} {action:<12} n={metrics['n']:<4} "
                f"win={metrics['win_rate']}%  exp={metrics['expectancy']}R  "
                f"total={metrics['total_r']}R"
            )


async def _run(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient() as client:
        candles = await load_history(client, args.symbol, args.timeframe, args.days)
    if not candles:
        raise SystemExit("aucune bougie récupérée")

    is_part, oos_part, oos_start = split_chrono(candles)
    print(
        f"=== {args.symbol} {args.timeframe}m | {len(candles)} bougies | "
        f"{_fmt(candles[0].open_time)} -> {_fmt(candles[-1].open_time)} UTC | "
        f"min_score={args.min_score} | stage={args.stage} ==="
    )

    if args.stage == "is":
        # IS = du début à J-550 (réglages/figeage autorisés).
        print(
            f"IS : {_fmt(candles[0].open_time)} -> {_fmt(oos_start)} "
            f"({len(is_part)} bougies)"
        )
        for k in MULTIPLIERS:
            _print_k(is_part, k, args.min_score, args.fee, candles[0].open_time)
            print()
        return

    # OOS — scellé : k figé à l'issue de l'IS, UNE seule consommation.
    if args.mult is None:
        raise SystemExit("stage oos : --mult <k figé> obligatoire (spec §4-5)")
    if args.mult not in MULTIPLIERS:
        raise SystemExit(f"k {args.mult} hors grille figée {MULTIPLIERS}")
    print(
        f"OOS : {_fmt(oos_start)} -> {_fmt(candles[-1].close_time)} "
        f"({len(oos_part)} bougies, warmup {WARMUP_DAYS} j exclu) — verdict final"
    )
    for k in (1, args.mult):  # k=1 = contrôle pour le gate G5
        _print_k(oos_part, k, args.min_score, args.fee, oos_start)
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Étude « SL élargi » momentum_v1 (aucun envoi, spec SL_ELARGI.md)."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="15", choices=list(INTERVAL_MS))
    parser.add_argument("--days", type=int, default=1490)
    parser.add_argument("--min-score", type=int, default=45)
    parser.add_argument("--fee", type=float, default=FEE_TAKER_RT)
    parser.add_argument("--stage", choices=("is", "oos"), required=True)
    parser.add_argument(
        "--mult", type=int, choices=MULTIPLIERS, help="k figé (obligatoire en OOS)"
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
