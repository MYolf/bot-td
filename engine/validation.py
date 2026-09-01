"""Validation anti-overfitting : découpage IS/OOS scellé + walk-forward.

Protocole (CONFLUENCE.md, skill backtesting) :

- **Découpage AVANT tout réglage** (décision de l'étape 3) : IS = les
  ``is_days`` derniers jours (déjà diagnostiqués en étape 3, mais AUCUN
  paramètre n'y a été réglé) ; OOS = la fenêtre antérieure, jamais ouverte.
  Limite assumée et documentée : l'OOS précède l'IS dans le temps (c'est la
  seule réserve vierge disponible) — la vraie confirmation viendra du
  walk-forward puis du paper trading.
- Chaque fenêtre reçoit un préfixe de ``warmup_days`` pour l'amorce des
  indicateurs (EMA 200 etc.) ; seuls les trades OUVERTS dans la fenêtre
  utile comptent dans les métriques.
- Le cache local (``data/cache/``, non versionné) évite de re-solliciter
  Binance entre deux expériences sur les mêmes données.

Usage :
    python -m engine.validation --symbol BTCUSDT --days 195 --stage is
    python -m engine.validation --symbol BTCUSDT --days 195 --stage oos
    python -m engine.validation --symbol BTCUSDT --days 195 --stage wf
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from engine.backtest import gather_history
from engine.binance_client import INTERVAL_MS
from engine.confluence import ConfluenceParams, confluence_signals, resample
from engine.confluence_backtest import (
    ExitPolicy,
    SignalEntry,
    Trade,
    compute_metrics,
    simulate_trades,
)
from engine.strategy import Candle

DAY_MS = 86_400_000
DEFAULT_CACHE_DIR = Path("data/cache")


# ------------------------------------------------------------------ données --


def _cache_path(cache_dir: Path, symbol: str, timeframe: str, days: int) -> Path:
    return cache_dir / f"{symbol}_{timeframe}m_{days}d.json"


def save_cache(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(c) for c in candles]
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_cache(path: Path) -> list[Candle] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return [Candle(**row) for row in payload]


async def load_history(
    client: httpx.AsyncClient,
    symbol: str,
    timeframe: str,
    days: int,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> list[Candle]:
    """Charge depuis le cache sinon récupère chez Binance puis met en cache."""
    path = _cache_path(cache_dir, symbol, timeframe, days)
    cached = load_cache(path)
    if cached is not None:
        return cached
    candles = await gather_history(client, symbol, timeframe, days)
    if candles:
        save_cache(path, candles)
    return candles


# ----------------------------------------------------------------- fenêtres --


def split_is_oos(
    candles: list[Candle],
    is_days: int,
    oos_days: int,
    warmup_days: int = 20,
) -> tuple[list[Candle], list[Candle], int, int]:
    """Coupe l'historique en (IS récent, OOS ancien scellé).

    Retourne ``(is_part, oos_part, is_start_ms, oos_start_ms)`` : chaque part
    inclut son préfixe de warmup ; ``*_start_ms`` borne le début de la fenêtre
    UTILE (les trades ouverts avant sont exclus des métriques).
    """
    if not candles:
        raise ValueError("historique vide")
    end_ms = candles[-1].close_time + 1
    is_start = end_ms - is_days * DAY_MS
    oos_end = is_start
    oos_start = oos_end - oos_days * DAY_MS
    warmup = warmup_days * DAY_MS
    is_part = [c for c in candles if c.open_time >= is_start - warmup]
    oos_part = [
        c for c in candles if c.open_time >= oos_start - warmup and c.close_time < oos_end
    ]
    if not is_part or not oos_part:
        raise ValueError("fenêtres IS/OOS vides : historique trop court")
    return is_part, oos_part, is_start, oos_start


def trades_in_window(
    candles: list[Candle], trades: list[Trade], window_start_ms: int
) -> list[Trade]:
    """Trades ouverts dans la fenêtre utile (exclut le warmup)."""
    return [t for t in trades if candles[t.open_index].open_time >= window_start_ms]


# -------------------------------------------------------------- évaluation --


@dataclass(frozen=True)
class Variant:
    """Une variante testable : profil de moteur + sortie + seuil de score."""

    name: str
    params: ConfluenceParams
    policy: ExitPolicy = ExitPolicy()
    min_score: int = 0
    htf_bucket_ms: int = 3_600_000


def evaluate(
    candles: list[Candle],
    variant: Variant,
    fee_rate: float = 0.0012,
    window_start_ms: int | None = None,
) -> list[Trade]:
    """Trades d'une variante (warmup exclu si ``window_start_ms`` fourni)."""
    htf = resample(candles, variant.htf_bucket_ms)
    signals = confluence_signals(candles, htf, variant.params)
    entries = [
        SignalEntry(
            index=s.index,
            action=s.action,
            entry=s.entry,
            stop_loss=s.stop_loss,
            take_profit=s.take_profit,
            score=s.score,
            trigger=s.trigger,
        )
        for s in signals
        if s.score >= variant.min_score
    ]
    trades = simulate_trades(candles, entries, fee_rate, variant.policy)
    if window_start_ms is not None:
        trades = trades_in_window(candles, trades, window_start_ms)
    return trades


def walk_forward(
    candles: list[Candle],
    variants: list[Variant],
    fee_rate: float = 0.0012,
    train_days: int = 60,
    test_days: int = 30,
    warmup_days: int = 20,
    min_train_trades: int = 20,
) -> tuple[list[dict], list[Trade]]:
    """Walk-forward : sélection sur fenêtre d'entraînement, test scellé.

    Pour chaque fenêtre : la variante de meilleure expectancy (avec au moins
    ``min_train_trades`` trades d'entraînement) est évaluée sur la fenêtre de
    test suivante. Retourne (rapport par fenêtre, trades de test recousus).
    """
    end_ms = candles[-1].close_time + 1
    warmup = warmup_days * DAY_MS
    report: list[dict] = []
    stitched: list[Trade] = []
    train_start = candles[0].open_time
    while True:
        train_end = train_start + train_days * DAY_MS
        test_end = train_end + test_days * DAY_MS
        if test_end > end_ms:
            break
        train_candles = [
            c for c in candles if c.open_time >= train_start - warmup and c.close_time < train_end
        ]
        test_candles = [
            c for c in candles if c.open_time >= train_end - warmup and c.close_time < test_end
        ]
        best_name = None
        best_metrics: dict[str, float | int] = {}
        if train_candles and test_candles:
            for variant in variants:
                trades = evaluate(train_candles, variant, fee_rate, train_start)
                metrics = compute_metrics(trades)
                if metrics.get("n", 0) < min_train_trades:
                    continue
                if best_name is None or metrics["expectancy"] > best_metrics["expectancy"]:
                    best_name = variant.name
                    best_metrics = metrics
        if best_name is not None:
            variant = next(v for v in variants if v.name == best_name)
            test_trades = evaluate(test_candles, variant, fee_rate, train_end)
            stitched.extend(test_trades)
            report.append(
                {
                    "train_start": train_start,
                    "test_start": train_end,
                    "variant": best_name,
                    "train": best_metrics,
                    "test": compute_metrics(test_trades),
                }
            )
        train_start = train_end
    return report, stitched


# --------------------------------------------------------------------- CLI --


def _fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _print_variants(candles: list[Candle], variants: list[Variant], fee: float, window_start: int) -> None:
    for variant in variants:
        trades = evaluate(candles, variant, fee, window_start)
        metrics = compute_metrics(trades)
        if metrics.get("n", 0) == 0:
            print(f"{variant.name:<28} aucun trade")
            continue
        print(
            f"{variant.name:<28} n={metrics['n']:<4} win={metrics['win_rate']}%  "
            f"exp={metrics['expectancy']}R  PF={metrics['profit_factor']}  "
            f"total={metrics['total_r']}R  DD={metrics['max_drawdown']}R"
        )


async def _run(args: argparse.Namespace) -> None:
    variants = _stage_variants(args.stage, args.timeframe)
    async with httpx.AsyncClient() as client:
        candles = await load_history(client, args.symbol, args.timeframe, args.days)
    if not candles:
        raise SystemExit("aucune bougie récupérée")
    print(
        f"=== {args.symbol} {args.timeframe}m | {len(candles)} bougies | "
        f"{_fmt(candles[0].open_time)} -> {_fmt(candles[-1].open_time)} UTC | "
        f"stage={args.stage} | frais aller-retour {100 * args.fee:.3f}% ==="
    )

    if args.stage == "wf":
        report, stitched = walk_forward(candles, variants, args.fee)
        for row in report:
            test = row["test"]
            print(
                f"test {_fmt(row['test_start'])} : choix={row['variant']:<16} "
                f"n={test.get('n', 0):<3} exp={test.get('expectancy', 0.0)}R "
                f"total={test.get('total_r', 0.0)}R"
            )
        print("\nwalk-forward agrégé (test uniquement) :")
        metrics = compute_metrics(stitched)
        for key, value in metrics.items():
            print(f"  {key} = {value}")
        return

    is_part, oos_part, is_start, oos_start = split_is_oos(candles, args.is_days, args.oos_days)
    if args.stage == "is":
        print(f"IS : {_fmt(is_start)} -> {_fmt(candles[-1].close_time)} (réglages autorisés)")
        _print_variants(is_part, variants, args.fee, is_start)
    else:  # oos — scellé : à ne lancer qu'UNE fois, verdict final
        print(f"OOS : {_fmt(oos_start)} -> {_fmt(is_start)} (verdict, aucun réglage)")
        _print_variants(oos_part, variants, args.fee, oos_start)


def _stage_variants(stage: str, timeframe: str) -> list[Variant]:
    """Variantes par étape ; OOS/walk-forward n'utilisent QUE les finalistes."""
    from engine.confluence import STRATEGY_PROFILES

    htf = 14_400_000 if timeframe == "60" else 3_600_000
    exploratoire = [
        Variant(
            f"{name}/{policy.kind}{policy.max_bars if policy.kind == 'time' else ''}",
            params,
            policy,
            0,
            htf,
        )
        for name, params in STRATEGY_PROFILES.items()
        for policy in (ExitPolicy(), ExitPolicy("time", 16), ExitPolicy("time", 32))
    ]
    if stage == "is":
        return exploratoire
    return FINAL_VARIANTS(timeframe)


def FINAL_VARIANTS(timeframe: str) -> list[Variant]:
    """Finalistes FIGÉS après réglage IS (2026-09-01, voir AVANCEMENT.md).

    Choix sur arguments structurants, jamais sur le meilleur bucket :
    - 1H : frais ~0.25 R/trade contre ~0.5 R en 15m (risque proportionnel
      au prix plus grand à timeframe plus élevé) ;
    - smc_v1 : sweep + retest FVG, seuls déclencheurs à dérive mesurée
      dans l'étude de features (étape 2) ;
    - bracket (sémantique identique au pipeline actuel) ET sortie
      temporelle 16 bougies (capture de la dérive) ;
    - contrôle 15m confluence_v0/bracket : la thèse prédit qu'il perd.
    """
    from engine.confluence import STRATEGY_PROFILES

    if timeframe == "60":
        return [
            Variant("smc_v1/bracket", STRATEGY_PROFILES["smc_v1"], ExitPolicy(), 0, 14_400_000),
            Variant("smc_v1/time16", STRATEGY_PROFILES["smc_v1"], ExitPolicy("time", 16), 0, 14_400_000),
        ]
    return [
        Variant("confluence_v0/bracket", STRATEGY_PROFILES["confluence_v0"], ExitPolicy(), 0, 3_600_000),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validation IS/OOS et walk-forward du moteur de confluence (aucun envoi)."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="15", choices=list(INTERVAL_MS))
    parser.add_argument("--days", type=int, default=195, help="total = oos + is + warmup")
    parser.add_argument("--is-days", type=int, default=90)
    parser.add_argument("--oos-days", type=int, default=90)
    parser.add_argument("--fee", type=float, default=0.0012)
    parser.add_argument("--stage", choices=("is", "oos", "wf"), required=True)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
