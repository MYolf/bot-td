"""Backtest comparatif : confluence v0 vs baseline momentum_v1, avec frais.

Règles de simulation (identiques pour les deux stratégies) :
- entrée à la CLÔTURE de la bougie de signal (aucune anticipation) ;
- une seule position à la fois ; un signal du même sens est ignoré, un
  signal opposé renverse la position (clôture à l'entrée du nouveau) ;
- sortie selon la politique ``ExitPolicy`` : bracket (SL/TP, SL prioritaire
  si les deux — hypothèse prudente, cohérente avec engine/position.py) ou
  temporelle (SL de sécurité seul + clôture h bougies plus tard) ;
- frais par aller-retour déduits en R (défaut 0.12 % : 2 x 0.05 % taker +
  slippage) ;
- position encore ouverte à la fin : clôturée à la dernière clôture.

Mesures en R-multiples : expectancy, profit factor, drawdown, séries de
pertes, et découpage par tiers de score (le seuil d'émission sera choisi
sur ces données, jamais à la main).

Usage :
    python -m engine.confluence_backtest --symbol BTCUSDT --days 90
    python -m engine.confluence_backtest --symbol ETHUSDT --days 180 --fee 0.0015
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from engine.backtest import gather_history, replay
from engine.binance_client import INTERVAL_MS
from engine.confluence import ConfluenceParams, confluence_signals, resample
from engine.strategy import Candle, MomentumParams

HTF_BUCKET_MS = 3_600_000  # contexte 1H


@dataclass(frozen=True)
class SignalEntry:
    """Ordre simulé à la clôture de la bougie ``index``."""

    index: int
    action: str
    entry: float
    stop_loss: float
    take_profit: float
    score: int = 0
    trigger: str = ""


@dataclass(frozen=True)
class ExitPolicy:
    """Politique de sortie du simulateur.

    - ``bracket`` (défaut) : SL ou TP dès qu'une bougie les touche (SL
      prioritaire) — logique de l'étape 3, détruit le drift mesuré en
      feature study si le bracket est trop serré.
    - ``time`` : SL de sécurité uniquement (le SL structurel du signal),
      sortie à la CLÔTURE de la bougie ``max_bars`` après l'entrée —
      capte la dérive conditionnelle mesurée (h=16) sans l'amputer.
    """

    kind: str = "bracket"
    max_bars: int = 16

    def __post_init__(self) -> None:
        if self.kind not in ("bracket", "time"):
            raise ValueError(f"politique de sortie inconnue : {self.kind}")
        if self.kind == "time" and self.max_bars < 1:
            raise ValueError("max_bars doit être >= 1")


@dataclass(frozen=True)
class Trade:
    open_index: int
    exit_index: int
    action: str
    trigger: str
    score: int
    raw_r: float
    net_r: float  # après frais


def simulate_trades(
    candles: list[Candle],
    entries: list[SignalEntry],
    fee_rate: float = 0.0012,
    policy: ExitPolicy | None = None,
) -> list[Trade]:
    """Simule les entrées dans l'ordre (une position, renversement autorisé)."""
    policy = policy or ExitPolicy()
    queue = sorted(entries, key=lambda e: e.index)
    trades: list[Trade] = []
    open_position: SignalEntry | None = None

    def _finalize(entry: SignalEntry, exit_index: int, exit_price: float) -> None:
        risk = abs(entry.entry - entry.stop_loss)
        if risk <= 0.0:
            return
        signed = 1.0 if entry.action == "BUY" else -1.0
        raw_r = signed * (exit_price - entry.entry) / risk
        net_r = raw_r - entry.entry * fee_rate / risk
        trades.append(
            Trade(
                open_index=entry.index,
                exit_index=exit_index,
                action=entry.action,
                trigger=entry.trigger,
                score=entry.score,
                raw_r=raw_r,
                net_r=net_r,
            )
        )

    cursor = 0
    for i, candle in enumerate(candles):
        if open_position is not None:
            exit_price: float | None = None
            if open_position.action == "BUY":
                if candle.low <= open_position.stop_loss:
                    exit_price = open_position.stop_loss
                elif policy.kind == "bracket" and candle.high >= open_position.take_profit:
                    exit_price = open_position.take_profit
            else:
                if candle.high >= open_position.stop_loss:
                    exit_price = open_position.stop_loss
                elif policy.kind == "bracket" and candle.low <= open_position.take_profit:
                    exit_price = open_position.take_profit
            if exit_price is None and policy.kind == "time" and i >= open_position.index + policy.max_bars:
                exit_price = candle.close  # sortie temporelle à la clôture
            if exit_price is not None:
                _finalize(open_position, i, exit_price)
                open_position = None

        while cursor < len(queue) and queue[cursor].index == i:
            candidate = queue[cursor]
            cursor += 1
            if open_position is not None:
                if open_position.action == candidate.action:
                    continue  # même sens : ignoré (pyramiding 0)
                _finalize(open_position, i, candidate.entry)  # renversement
                open_position = None
            open_position = candidate

    if open_position is not None:
        _finalize(open_position, len(candles) - 1, candles[-1].close)
    return trades


def compute_metrics(trades: list[Trade]) -> dict[str, float | int]:
    """Métriques en R (voir skill backtesting : le couple WR x RR compte)."""
    if not trades:
        return {"n": 0}
    net = [t.net_r for t in trades]
    wins = [r for r in net if r > 0.0]
    losses = [r for r in net if r <= 0.0]
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    worst_streak = 0
    for r in net:
        cumulative += r
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
        streak = streak + 1 if r <= 0.0 else 0
        worst_streak = max(worst_streak, streak)
    return {
        "n": len(trades),
        "win_rate": round(100.0 * len(wins) / len(net), 1),
        "expectancy": round(statistics.fmean(net), 3),
        "median_r": round(statistics.median(net), 3),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses else float("inf"),
        "total_r": round(sum(net), 2),
        "max_drawdown": round(max_dd, 2),
        "max_losing_streak": worst_streak,
    }


def _fmt_time(open_time_ms: int) -> str:
    return datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _print_block(title: str, metrics: dict[str, float | int]) -> None:
    if metrics.get("n", 0) == 0:
        print(f"{title}: aucun trade")
        return
    print(
        f"{title}:\n"
        f"  trades={metrics['n']}  win={metrics['win_rate']}%  "
        f"expectancy={metrics['expectancy']}R  median={metrics['median_r']}R\n"
        f"  PF={metrics['profit_factor']}  total={metrics['total_r']}R  "
        f"maxDD={metrics['max_drawdown']}R  serie_pertes={metrics['max_losing_streak']}"
    )


def run_comparison(
    candles: list[Candle],
    fee_rate: float,
    params: ConfluenceParams | None = None,
    momentum_params: MomentumParams | None = None,
) -> tuple[list[Trade], list[Trade]]:
    """Retourne (trades momentum_v1, trades confluence_v0)."""
    index_by_open_time = {c.open_time: i for i, c in enumerate(candles)}

    baseline_signals, _ignored = replay(candles, momentum_params)
    baseline_entries = [
        SignalEntry(
            index=index_by_open_time[s.candle_open_time],
            action=s.action,
            entry=s.entry,
            stop_loss=s.stop_loss,
            take_profit=s.take_profit,
            trigger="momentum_v1",
        )
        for s in baseline_signals
    ]

    htf = resample(candles, HTF_BUCKET_MS)
    confluence = confluence_signals(candles, htf, params)
    confluence_entries = [
        SignalEntry(
            index=s.index,
            action=s.action,
            entry=s.entry,
            stop_loss=s.stop_loss,
            take_profit=s.take_profit,
            score=s.score,
            trigger=s.trigger,
        )
        for s in confluence
    ]

    return (
        simulate_trades(candles, baseline_entries, fee_rate),
        simulate_trades(candles, confluence_entries, fee_rate),
    )


async def _run(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient() as client:
        candles = await gather_history(client, args.symbol, args.timeframe, args.days)
    if not candles:
        raise SystemExit("aucune bougie récupérée")
    print(
        f"=== {args.symbol} {args.timeframe}m | {len(candles)} bougies fermées | "
        f"{_fmt_time(candles[0].open_time)} -> {_fmt_time(candles[-1].open_time)} UTC | "
        f"frais aller-retour {100 * args.fee:.3f}% ==="
    )
    baseline, confluence = run_comparison(candles, args.fee)

    _print_block("momentum_v1 (baseline)", compute_metrics(baseline))
    print()
    _print_block("confluence_v0 (tous scores)", compute_metrics(confluence))
    print()

    print("confluence_v0 par tiers de score (choix du seuil d'emission) :")
    for label, keep in (
        ("score < 60", lambda s: s < 60),
        ("60-79", lambda s: 60 <= s < 80),
        (">= 80", lambda s: s >= 80),
    ):
        _print_block(f"  {label}", compute_metrics([t for t in confluence if keep(t.score)]))
    print()

    print("confluence_v0 par declencheur :")
    for trigger in ("sweep", "bos", "ob_retest", "fvg_retest"):
        _print_block(f"  {trigger}", compute_metrics([t for t in confluence if t.trigger == trigger]))
    print()

    if args.details:
        for t in confluence:
            print(
                f"  {_fmt_time(candles[t.open_index].open_time)} {t.action:<4} "
                f"{t.trigger:<10} score={t.score:<3} net={t.net_r:+.2f}R "
                f"(sortie {_fmt_time(candles[t.exit_index].open_time)})"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest comparatif confluence v0 vs momentum_v1 (aucun envoi)."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="15", choices=list(INTERVAL_MS))
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--fee", type=float, default=0.0012)
    parser.add_argument("--details", action="store_true", help="liste des trades")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
