"""Étude de viabilité des features : forward returns conditionnels.

Outil de RECHERCHE uniquement (aucun envoi de webhook) — étape 1 du protocole
anti-overfitting de CONFLUENCE.md : valider chaque feature individuellement
AVANT de l'intégrer à un score ou une stratégie.

Pour chaque état de feature, mesure le rendement futur (4 / 16 / 48 bougies,
soit ~1 h / 4 h / 12 h en 15m) et le compare au rendement inconditionnel.

Usage :
    python -m engine.feature_study --symbol BTCUSDT --days 180
    python -m engine.feature_study --symbol ETHUSDT --timeframe 60 --days 365

Lecture (rappel) : un aller-retour réaliste coûte ~0.10-0.15 % (taker 2 x
0.05 % + slippage). Une feature dont le meilleur état affiche un forward
return moyen inférieur à ce seuil n'est PAS exploitable telle quelle.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from datetime import datetime, timezone

import httpx

from engine.backtest import gather_history
from engine.binance_client import INTERVAL_MS
from engine.features import (
    momentum_states,
    trend_states,
    volume_states,
    vwap_daily,
)
from engine.strategy import Candle
from engine.structure import liquidity_sweeps, market_structure
from engine.zones import displacements, fair_value_gaps, order_blocks

HORIZONS: tuple[int, ...] = (4, 16, 48)  # bougies
RVOL_BUCKETS: tuple[tuple[str, float | None, float | None], ...] = (
    ("rvol<0.8", None, 0.8),
    ("0.8-1.2", 0.8, 1.2),
    ("1.2-2.0", 1.2, 2.0),
    ("rvol>=2.0", 2.0, None),
)


def forward_returns(closes: list[float], horizon: int) -> list[float | None]:
    """Rendement futur sur ``horizon`` bougies ; None près de la fin."""
    out: list[float | None] = [None] * len(closes)
    for i in range(len(closes) - horizon):
        out[i] = closes[i + horizon] / closes[i] - 1.0
    return out


def _group_stats(
    label: str,
    indices: list[int],
    returns_by_h: dict[int, list[float | None]],
) -> list[str]:
    lines = [f"  {label:<28} n={len(indices):<6}"]
    for h in HORIZONS:
        values = [returns_by_h[h][i] for i in indices if returns_by_h[h][i] is not None]
        if not values:
            lines.append(f"    h={h:<3} n/a")
            continue
        mean = 100.0 * statistics.fmean(values)
        median = 100.0 * statistics.median(values)
        win = 100.0 * sum(1 for v in values if v > 0.0) / len(values)
        lines.append(
            f"    h={h:<3} mean={mean:+.3f}%  median={median:+.3f}%  "
            f"win={win:.1f}%  n={len(values)}"
        )
    return lines


def _grouped_by(value_of, returns_by_h, name: str) -> list[str]:
    groups: dict[str, list[int]] = {}
    for i, value in enumerate(value_of):
        key = str(value)
        groups.setdefault(key, []).append(i)
    lines = [f"\n{name}"]
    for key in sorted(groups):
        lines.extend(_group_stats(key, groups[key], returns_by_h))
    return lines


def _rvol_label(rvol: float | None) -> str:
    if rvol is None:
        return "na"
    for name, low, high in RVOL_BUCKETS:
        if (low is None or rvol >= low) and (high is None or rvol < high):
            return name
    return "na"


def run_study(candles: list[Candle]) -> str:
    """Calcule et formate l'étude complète (pure, testable)."""
    closes = [c.close for c in candles]
    returns_by_h = {h: forward_returns(closes, h) for h in HORIZONS}
    lines: list[str] = [
        f"{len(candles)} bougies fermées, "
        f"{datetime.fromtimestamp(candles[0].open_time / 1000, tz=timezone.utc):%Y-%m-%d}"
        f" -> {datetime.fromtimestamp(candles[-1].open_time / 1000, tz=timezone.utc):%Y-%m-%d} UTC",
        "Rappel : un aller-retour coute ~0.10-0.15 % en frais+slippage.",
    ]
    all_indices = list(range(len(candles)))
    lines.append("\nBASELINE (toutes bougies)")
    lines.extend(_group_stats("toutes", all_indices, returns_by_h))

    lines.extend(_grouped_by([s.bias for s in trend_states(candles)], returns_by_h, "TREND 15m (EMA fusion)"))
    lines.extend(_grouped_by([s.bias for s in momentum_states(candles)], returns_by_h, "MOMENTUM (RSI+MACD fusion)"))

    structure = market_structure(candles)
    lines.extend(_grouped_by(structure.bias, returns_by_h, "STRUCTURE (dernier BOS)"))
    bos_indices = [e.index for e in structure.events]
    lines.append("\nBOS (evenements)")
    lines.extend(_group_stats("tous BOS", bos_indices, returns_by_h))
    for kind in ("bos_bullish", "bos_bearish"):
        indices = [e.index for e in structure.events if e.kind == kind]
        lines.extend(_group_stats(kind, indices, returns_by_h))

    disp = displacements(candles)
    lines.append("\nDISPLACEMENT (evenements)")
    for direction in ("bullish", "bearish"):
        lines.extend(
            _group_stats(direction, [e.index for e in disp if e.direction == direction], returns_by_h)
        )

    sweeps = liquidity_sweeps(candles)
    lines.append("\nLIQUIDITY SWEEPS (evenements)")
    for direction in ("bullish", "bearish"):
        lines.extend(
            _group_stats(direction, [e.index for e in sweeps if e.direction == direction], returns_by_h)
        )

    gaps = fair_value_gaps(candles)
    lines.append("\nFVG (premier retest, +/- displacement a la creation)")
    for direction in ("bullish", "bearish"):
        for with_disp in (True, False):
            label = f"{direction} {'avec' if with_disp else 'sans'} disp."
            indices = [
                g.first_retest_index
                for g in gaps
                if g.direction == direction
                and g.with_displacement == with_disp
                and g.first_retest_index is not None
            ]
            lines.extend(_group_stats(label, indices, returns_by_h))

    blocks = order_blocks(candles)
    lines.append("\nORDER BLOCKS (premier retest apres BOS)")
    for direction in ("bullish", "bearish"):
        lines.extend(
            _group_stats(
                direction,
                [
                    b.first_retest_index
                    for b in blocks
                    if b.direction == direction and b.first_retest_index is not None
                ],
                returns_by_h,
            )
        )

    lines.extend(
        _grouped_by(
            [_rvol_label(s.rvol) for s in volume_states(candles)], returns_by_h, "RVOL"
        )
    )
    vwap = vwap_daily(candles)
    sides = [
        "na" if v is None else ("above" if closes[i] > v else "below")
        for i, v in enumerate(vwap)
    ]
    lines.extend(_grouped_by(sides, returns_by_h, "VWAP journalier (position du prix)"))
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient() as client:
        candles = await gather_history(client, args.symbol, args.timeframe, args.days)
    if not candles:
        raise SystemExit("aucune bougie récupérée")
    print(f"=== {args.symbol} {args.timeframe}m ===")
    print(run_study(candles))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Étude forward returns conditionnels des features (recherche, aucun envoi)."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="15", choices=list(INTERVAL_MS))
    parser.add_argument("--days", type=int, default=180)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
