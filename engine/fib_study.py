"""Étude d'événements : retest d'OB x confluence Fibonacci (Phase 32, étape 2).

Outil de RECHERCHE uniquement (aucun envoi, aucune décision de trading) —
teste l'hypothèse scellée FIBONACCI.md §5 : un retest d'Order Block de grade
``inclusion``/``overlap`` avec la bande 0.50-0.70 d'un Fib actif de même
direction a un forward return supérieur à un retest d'OB sans cette
confluence.

Aucune modification de production (FIBONACCI.md §8) : l'étude joint les
signaux de ``confluence_signals`` (profil ``confluence_v0`` inchangé) aux
états ``fib_states`` par index de bougie. Découpage IS/OOS scellé via
``engine/validation.py`` ; l'OOS ne doit être lancé QU'UNE fois, après revue
de l'IS.

Usage :
    python -m engine.fib_study --symbol BTCUSDT --timeframe 15 --days 195 --stage is
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from engine.confluence import (
    BEARISH,
    BULLISH,
    ConfluenceParams,
    confluence_signals,
    resample,
)
from engine.fibonacci import ACTIVE, FibParams, fib_states, ob_fib_grade
from engine.indicators import atr as atr_series
from engine.strategy import Candle
from engine.validation import load_history, split_is_oos
from engine.zones import order_blocks

HORIZONS: tuple[int, ...] = (4, 16, 48)  # bougies (comme feature_study.py)
GRADE_RANK: dict[str, int] = {"none": 0, "proximity": 1, "overlap": 2, "inclusion": 3}
CRIT_MIN_N = 30  # seuil scellé FIBONACCI.md §7


@dataclass(frozen=True)
class StudyEvent:
    """Un déclenchement observé, annoté par la confluence Fibonacci."""

    index: int  # bougie de déclenchement
    trigger: str  # sweep / bos / ob_retest / fvg_retest
    action: str  # BUY / SELL
    group: str  # ob_fib / ob_alone (ou in_band / out_band pour les contrôles)
    grade: str  # inclusion / overlap / proximity / none (na pour les contrôles)
    depth: float | None  # retracement r du close via fib_states (None si pas de Fib)
    open_time: int
    risk_frac: float | None  # |entry - SL| / entry (bracket du signal)
    fwd: dict[int, float | None]  # horizon -> forward return ajuste du sens


# ----------------------------------------------------------- fonctions pures --


def forward_return(
    closes: list[float], index: int, action: str, horizon: int
) -> float | None:
    """Forward return ajuste du sens (BUY : tel quel, SELL : negation).

    ``None`` quand l'horizon depasse la fin de la serie.
    """
    if index + horizon >= len(closes):
        return None
    ret = closes[index + horizon] / closes[index] - 1.0
    return ret if action == "BUY" else -ret


def depth_bucket(depth: float | None) -> str:
    """Bucketisation scellee de la profondeur de retracement (question §5 bis)."""
    if depth is None:
        return "na"
    if depth < 0.5:
        return "<0.5"
    if depth < 0.7:
        return "0.5-0.7"
    if depth <= 1.0:
        return "0.7-1.0"
    return ">1.0"


def group_stats(events: list[StudyEvent], horizon: int) -> dict:
    """n / mean / median / win du forward return a ``horizon`` (sans frais)."""
    values = [e.fwd[horizon] for e in events if e.fwd.get(horizon) is not None]
    if not values:
        return {"n": 0, "mean": None, "median": None, "win": None}
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "win": sum(1 for v in values if v > 0.0) / len(values),
    }


def r_net_stats(
    events: list[StudyEvent], fee_rt: float, horizon: int = 16
) -> dict:
    """Mean R brut et net de frais : ``(fwd - fee_rt) / risk_frac``."""
    pairs = [
        (e.fwd[horizon], e.risk_frac)
        for e in events
        if e.fwd.get(horizon) is not None and e.risk_frac is not None and e.risk_frac > 0.0
    ]
    if not pairs:
        return {"n": 0, "mean_r": None, "mean_net_r": None}
    gross = [fwd / risk for fwd, risk in pairs]
    net = [(fwd - fee_rt) / risk for fwd, risk in pairs]
    return {
        "n": len(pairs),
        "mean_r": statistics.fmean(gross),
        "mean_net_r": statistics.fmean(net),
    }


def criterion(fib_stats: dict, alone_stats: dict) -> dict[str, bool]:
    """Booleens du critere scelle FIBONACCI.md §7 pour UN run (IS ou OOS).

    Chaque dict d'entree : ``n`` (h=16), ``mean`` (forward h=16 sans frais),
    ``r_net`` (mean R net de frais h=16).
    """
    def _delta(key: str) -> bool:
        a, b = fib_stats.get(key), alone_stats.get(key)
        return a is not None and b is not None and a - b > 0.0

    return {
        "n_min_30": fib_stats["n"] >= CRIT_MIN_N and alone_stats["n"] >= CRIT_MIN_N,
        "delta_mean_h16": _delta("mean"),
        "delta_r_net": _delta("r_net"),
    }


# ------------------------------------------------------------- construction --


def _make_event(
    candles: list[Candle],
    closes: list[float],
    signal,
    group: str,
    grade: str,
    depth: float | None,
) -> StudyEvent:
    risk_frac = (
        abs(signal.entry - signal.stop_loss) / signal.entry
        if signal.entry > 0.0
        else None
    )
    return StudyEvent(
        index=signal.index,
        trigger=signal.trigger,
        action=signal.action,
        group=group,
        grade=grade,
        depth=depth,
        open_time=candles[signal.index].open_time,
        risk_frac=risk_frac,
        fwd={h: forward_return(closes, signal.index, signal.action, h) for h in HORIZONS},
    )


def _best_grade(blocks: list, direction: str, state, atr: float | None) -> str:
    """Meilleur grade parmi les Order Blocks retests a cette bougie."""
    best = "none"
    for block in blocks:
        if block.direction != direction:
            continue
        grade = ob_fib_grade(block.direction, block.bottom, block.top, state, atr)
        if GRADE_RANK[grade] > GRADE_RANK[best]:
            best = grade
    return best


def build_events(
    candles: list[Candle],
    htf_candles: list[Candle] | None = None,
    params: ConfluenceParams | None = None,
) -> list[StudyEvent]:
    """Evenements ``ob_retest`` groupes ``ob_fib`` (inclusion/overlap) vs ``ob_alone``.

    Jointure par index de bougie : signaux ``confluence_signals`` (profil
    ``confluence_v0`` inchange) x Order Blocks ``first_retest_index`` x etats
    ``fib_states``. Aucune modification des signaux, seule annotation.
    """
    params = params or ConfluenceParams()
    closes = [c.close for c in candles]
    signals = confluence_signals(candles, htf_candles, params)
    states = fib_states(candles, FibParams())
    atrs = atr_series(
        [c.high for c in candles], [c.low for c in candles], closes, params.atr_len
    )
    blocks_by_retest: dict[int, list] = {}
    for block in order_blocks(candles, swing_k=params.swing_k, atr_len=params.atr_len):
        if block.first_retest_index is not None:
            blocks_by_retest.setdefault(block.first_retest_index, []).append(block)

    events: list[StudyEvent] = []
    for signal in signals:
        if signal.trigger != "ob_retest":
            continue
        direction = BULLISH if signal.action == "BUY" else BEARISH
        state = states[signal.index]
        grade = _best_grade(
            blocks_by_retest.get(signal.index, []),
            direction,
            state,
            atrs[signal.index],
        )
        group = "ob_fib" if grade in ("inclusion", "overlap") else "ob_alone"
        events.append(
            _make_event(
                candles, closes, signal, group, grade,
                state.depth if state is not None else None,
            )
        )
    return events


def control_events(
    candles: list[Candle],
    htf_candles: list[Candle] | None = None,
    params: ConfluenceParams | None = None,
) -> list[StudyEvent]:
    """Controles de specificite : sweep / bos / fvg_retest groupes par bande.

    ``in_band`` = Fib actif, meme direction que le signal, et la bougie de
    declenchement chevauche la zone 0.50-0.70 ; ``out_band`` sinon. Si l'effet
    Fib est specifique, seuls les retests d'OB doivent en profiter.
    """
    params = params or ConfluenceParams()
    closes = [c.close for c in candles]
    signals = confluence_signals(candles, htf_candles, params)
    states = fib_states(candles, FibParams())
    events: list[StudyEvent] = []
    for signal in signals:
        if signal.trigger == "ob_retest":
            continue
        direction = BULLISH if signal.action == "BUY" else BEARISH
        state = states[signal.index]
        in_band = (
            state is not None
            and state.status == ACTIVE
            and state.impulse.direction == direction
            and candles[signal.index].low <= state.zone_top
            and candles[signal.index].high >= state.zone_bottom
        )
        events.append(
            _make_event(
                candles, closes, signal,
                "in_band" if in_band else "out_band",
                "na",
                state.depth if state is not None else None,
            )
        )
    return events


# ------------------------------------------------------------------ edition --


def _htf_bucket(candles: list[Candle]) -> int | None:
    """Bucket HTF comme validation._stage_variants : 1H pour 15m, 4H pour 1H."""
    if len(candles) < 2:
        return None
    source_ms = candles[1].open_time - candles[0].open_time
    return {900_000: 3_600_000, 3_600_000: 14_400_000}.get(source_ms)


def _fmt_group(events: list[StudyEvent], label: str, fee_rt: float) -> list[str]:
    lines = [f"  {label}"]
    for h in HORIZONS:
        stats = group_stats(events, h)
        if stats["n"] == 0:
            lines.append(f"    h={h:<3} n=0")
            continue
        lines.append(
            f"    h={h:<3} n={stats['n']:<4} mean={100 * stats['mean']:+.3f}%  "
            f"median={100 * stats['median']:+.3f}%  win={100 * stats['win']:.1f}%"
        )
    net = r_net_stats(events, fee_rt)
    if net["n"] == 0:
        lines.append("    R h=16 : n/a")
    else:
        lines.append(
            f"    R h=16 : brut={net['mean_r']:+.3f}R  net={net['mean_net_r']:+.3f}R"
            f"  (frais A/R {100 * fee_rt:.3f}%)"
        )
    return lines


def _window_stats(events: list[StudyEvent], fee_rt: float) -> dict:
    stats16 = group_stats(events, 16)
    net = r_net_stats(events, fee_rt)
    return {"n": stats16["n"], "mean": stats16["mean"], "r_net": net["mean_net_r"]}


def _fmt_bool(value: bool) -> str:
    return "OUI" if value else "NON"


def run_study(candles: list[Candle], window_start_ms: int, fee_rt: float) -> str:
    """Etude complete formatee (pure, testable) sur la fenetre utile."""
    params = ConfluenceParams()
    htf_bucket = _htf_bucket(candles)
    htf = resample(candles, htf_bucket) if htf_bucket else None

    ob_events = [e for e in build_events(candles, htf, params) if e.open_time >= window_start_ms]
    fib_events = [e for e in ob_events if e.group == "ob_fib"]
    alone_events = [e for e in ob_events if e.group == "ob_alone"]

    first = datetime.fromtimestamp(candles[0].open_time / 1000, tz=timezone.utc)
    lines: list[str] = [
        f"{len(candles)} bougies (warmup inclus), depart cache {first:%Y-%m-%d} UTC ; "
        f"forward returns SANS frais (le R net traduit les frais).",
        f"evenements ob_retest dans la fenetre utile : {len(ob_events)} "
        f"(OB+Fib={len(fib_events)}, OB seul={len(alone_events)})",
    ]

    # 1. Table principale : hypothese §5.
    lines.append("\nOB RETEST : OB+Fib (inclusion/overlap) vs OB seul")
    lines.extend(_fmt_group(fib_events, "OB+Fib", fee_rt))
    lines.extend(_fmt_group(alone_events, "OB seul", fee_rt))

    # 2. Question secondaire : profondeur de retracement au retest.
    lines.append("\nPROFONDEUR AU RETEST (tous ob_retest, question secondaire §5)")
    buckets: dict[str, list[StudyEvent]] = {}
    for event in ob_events:
        buckets.setdefault(depth_bucket(event.depth), []).append(event)
    for name in ("<0.5", "0.5-0.7", "0.7-1.0", ">1.0", "na"):
        events = buckets.get(name, [])
        if not events:
            continue
        lines.extend(_fmt_group(events, name, fee_rt))

    # 3. Controles de specificite : les autres declencheurs ne doivent pas
    #    profiter de la bande (sinon l'effet n'est pas Fib-specifique).
    controls = [
        e for e in control_events(candles, htf, params) if e.open_time >= window_start_ms
    ]
    lines.append("\nCONTROLES DE SPECIFICITE (dans bande alignee vs hors bande)")
    for trigger in ("sweep", "bos", "fvg_retest"):
        subset = [e for e in controls if e.trigger == trigger]
        lines.append(f"  {trigger}")
        lines.extend(_fmt_group([e for e in subset if e.group == "in_band"], "dans bande", fee_rt))
        lines.extend(_fmt_group([e for e in subset if e.group == "out_band"], "hors bande", fee_rt))

    # 4. Rappel des booleens du critere scelle §7 pour CE run.
    fib_w = _window_stats(fib_events, fee_rt)
    alone_w = _window_stats(alone_events, fee_rt)
    verdict = criterion(fib_w, alone_w)
    lines.append("\nCRITERE §7 (ce run uniquement ; validation = IS ET OOS)")
    lines.append(
        f"  n >= {CRIT_MIN_N} dans chaque groupe : {_fmt_bool(verdict['n_min_30'])} "
        f"(OB+Fib n={fib_w['n']}, OB seul n={alone_w['n']})"
    )
    if fib_w["mean"] is not None and alone_w["mean"] is not None:
        delta = 100 * (fib_w["mean"] - alone_w["mean"])
        lines.append(f"  delta mean h=16 > 0 : {_fmt_bool(verdict['delta_mean_h16'])} ({delta:+.3f} pts de %)")
    else:
        lines.append(f"  delta mean h=16 > 0 : {_fmt_bool(verdict['delta_mean_h16'])} (donnees insuffisantes)")
    if fib_w["r_net"] is not None and alone_w["r_net"] is not None:
        delta_r = fib_w["r_net"] - alone_w["r_net"]
        lines.append(f"  delta R net > 0 : {_fmt_bool(verdict['delta_r_net'])} ({delta_r:+.3f}R)")
    else:
        lines.append(f"  delta R net > 0 : {_fmt_bool(verdict['delta_r_net'])} (donnees insuffisantes)")
    return "\n".join(lines)


# --------------------------------------------------------------------- CLI --


def _fmt_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


async def _run(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient() as client:
        candles = await load_history(client, args.symbol, args.timeframe, args.days)
    if not candles:
        raise SystemExit("aucune bougie récupérée")
    is_part, oos_part, is_start, oos_start = split_is_oos(
        candles, args.is_days, args.oos_days
    )
    if args.stage == "is":
        part, start, label = is_part, is_start, "IS"
        note = "réglages/lecture autorisés"
    else:
        print(
            "ATTENTION : OOS scellé (FIBONACCI.md §6) — à ne lancer QU'UNE fois, "
            "après figeage des conclusions sur l'IS. Ce run est le verdict."
        )
        part, start, label = oos_part, oos_start, "OOS"
        note = "verdict, aucun réglage"
    print(
        f"=== {args.symbol} {args.timeframe}m | stage={label.upper()} | {note} | "
        f"{_fmt_date(part[0].open_time)} -> {_fmt_date(part[-1].open_time)} UTC | "
        f"frais aller-retour {100 * args.fee:.3f}% ==="
    )
    print(run_study(part, start, args.fee))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Étude d'événements OB x Fibonacci (recherche, aucun envoi)."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="15", choices=("15", "60"))
    parser.add_argument("--days", type=int, default=195, help="total = oos + is + warmup")
    parser.add_argument("--is-days", type=int, default=90)
    parser.add_argument("--oos-days", type=int, default=90)
    parser.add_argument("--fee", type=float, default=0.0012)
    parser.add_argument("--stage", choices=("is", "oos"), required=True)
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
