"""Event study macro — Phase A : le GATE (MACRO.md §8).

Question posée : « quels événements changent réellement le comportement du
marché ? » Mesure, par type d'événement / symbole / timeframe :

- amplitude de bougie (high-low)/open en %,
- corps absolu |close-open|/open en %,
- volume,

autour de chaque événement (fenêtres pré [T-60, T-15), cœur [T-15, T+30),
post [T+30, T+120)), comparées à une baseline : bougies de la MÊME minute de
journée et du MÊME jour de semaine, sur des jours SANS aucun événement suivi
(contrôle des cycles journalier et hebdomadaire du crypto).

GATE scellé AVANT mesure : médiane d'amplitude du cœur >= 2x la baseline pour
FOMC et CPI, sur BTCUSDT ET ETHUSDT en 15m.
- OK  -> le blocage EXTREME est justifié (hygiène de risque).
- KO  -> NO-GO définitif du blocage (au maximum un affichage informatif).

Une bougie appartient à une fenêtre si son intervalle [open, close] a une
intersection non vide avec elle (les bougies 1H qui CONTIENNENT l'événement
sont donc comptées dans le cœur).

Usage :

    python -m engine.macro_study                      # gate : BTC+ETH, 15m+1H
    python -m engine.macro_study --days 1490 --events data/macro/events.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from engine.binance_client import INTERVAL_MS
from engine.macro.calendar import events_from_records
from engine.strategy import Candle
from engine.validation import load_history

logger = logging.getLogger(__name__)

MINUTE_MS = 60_000

# Fenêtres d'étude en minutes signées relatives à l'événement (T = annonce).
STUDY_WINDOWS: dict[str, tuple[int, int]] = {
    "pre": (-60, -15),
    "core": (-15, 30),
    "post": (30, 120),
}

# Gate scellé (MACRO.md §8) — ne pas modifier sans motive documenté.
GATE_TYPES = ("FOMC", "CPI")
GATE_SYMBOLS = ("BTCUSDT", "ETHUSDT")
GATE_TIMEFRAME = "15"
GATE_RATIO = 2.0

DEFAULT_EVENTS = Path("data/macro/events.json")
DEFAULT_TYPES = ("FOMC", "CPI", "NFP", "PPI")


# ------------------------------------------------------------- métriques --


def range_pct(candle: Candle) -> float:
    return (candle.high - candle.low) / candle.open * 100


def body_pct(candle: Candle) -> float:
    return abs(candle.close - candle.open) / candle.open * 100


def candle_in_window(candle: Candle, event_ms: int, start_min: int, end_min: int) -> bool:
    """Intersection non vide entre [open_time, close_time] et la fenêtre.

    Convention demi-ouverte sur la fenêtre : une bougie ouverte exactement à
    la borne de fin n'appartient PAS à la fenêtre.
    """
    win_start = event_ms + start_min * MINUTE_MS
    win_end = event_ms + end_min * MINUTE_MS
    return candle.open_time < win_end and candle.close_time >= win_start


# ------------------------------------------------------------- découpage --


def _event_ms(event) -> int:
    return int(event.scheduled_at.timestamp() * 1000)


def window_indices(
    candles: list[Candle], events: list, window: tuple[int, int]
) -> tuple[list[int], int]:
    """Indices de bougies dans la fenêtre (tous événements confondus) et
    nombre d'événements couverts (>= 1 bougie dans la fenêtre)."""
    indices: list[int] = []
    covered = 0
    for event in events:
        event_ms = _event_ms(event)
        n_event = 0
        for i, candle in enumerate(candles):
            if candle_in_window(candle, event_ms, window[0], window[1]):
                indices.append(i)
                n_event += 1
        if n_event:
            covered += 1
    return indices, covered


def event_days(all_events: list) -> set[date]:
    """Jours UTC portant au moins un événement suivi (exclus de la baseline)."""
    return {e.scheduled_at.date() for e in all_events}


def baseline_indices(
    candles: list[Candle], ref_indices: list[int], exclude_days: set[date]
) -> list[int]:
    """Bougies de référence : même (jour de semaine, minute de journée) que
    les bougies événement, sur des jours sans événement."""
    by_slot: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, candle in enumerate(candles):
        moment = datetime.fromtimestamp(candle.open_time / 1000, tz=timezone.utc)
        if moment.date() in exclude_days:
            continue
        by_slot[(moment.weekday(), moment.hour * 60 + moment.minute)].append(i)
    slots = set()
    for i in ref_indices:
        moment = datetime.fromtimestamp(candles[i].open_time / 1000, tz=timezone.utc)
        slots.add((moment.weekday(), moment.hour * 60 + moment.minute))
    out: list[int] = []
    seen: set[int] = set()
    for slot in slots:
        for i in by_slot.get(slot, ()):
            if i not in seen:
                seen.add(i)
                out.append(i)
    return out


# ----------------------------------------------------------------- étude --


def _ratio(event_values: list[float], base_values: list[float]) -> float | None:
    if not event_values or not base_values:
        return None
    base = statistics.median(base_values)
    if base <= 0:
        return None
    return statistics.median(event_values) / base


def study_type(
    candles: list[Candle], all_events: list, event_type: str
) -> dict[str, dict]:
    """Ratios médians événement/baseline par fenêtre, pour un type."""
    first_ms, last_ms = candles[0].open_time, candles[-1].close_time
    events = [
        e
        for e in all_events
        if e.event_type == event_type and first_ms <= _event_ms(e) <= last_ms
    ]
    exclude = event_days(all_events)
    result: dict[str, dict] = {"n_events": len(events)}
    for name, window in STUDY_WINDOWS.items():
        refs, covered = window_indices(candles, events, window)
        base = baseline_indices(candles, refs, exclude)
        med = lambda idx, f: [f(candles[i]) for i in idx]  # noqa: E731
        result[name] = {
            "n_events_covered": covered,
            "n_candles": len(refs),
            "n_baseline": len(base),
            "ratio_range": _ratio(med(refs, range_pct), med(base, range_pct)),
            "ratio_body": _ratio(med(refs, body_pct), med(base, body_pct)),
            "ratio_volume": _ratio(
                [candles[i].volume for i in refs], [candles[i].volume for i in base]
            ),
        }
    return result


def gate_pass(studies: dict[tuple[str, str, str], dict]) -> tuple[bool, list[str]]:
    """Gate scellé : cœur >= GATE_RATIO x baseline pour FOMC et CPI, sur
    BTCUSDT et ETHUSDT en 15m (4 combinaisons, toutes requises)."""
    details: list[str] = []
    ok = True
    for event_type in GATE_TYPES:
        for symbol in GATE_SYMBOLS:
            study = studies.get((symbol, GATE_TIMEFRAME, event_type))
            ratio = study["core"]["ratio_range"] if study else None
            passed = ratio is not None and ratio >= GATE_RATIO
            ok = ok and passed
            ratio_txt = f"x{ratio:.2f}" if ratio is not None else "n/a"
            details.append(
                f"{event_type:<5} {symbol:<8} 15m core range {ratio_txt:<7} "
                f"({'PASS' if passed else 'FAIL'}, seuil x{GATE_RATIO:.1f})"
            )
    return ok, details


# ------------------------------------------------------------------ CLI --


def _fmt(ratio: float | None) -> str:
    return f"x{ratio:.2f}" if ratio is not None else "n/a"


async def _run(args: argparse.Namespace) -> None:
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    types = [t.strip().upper() for t in args.types.split(",") if t.strip()]
    path = Path(args.events) if args.events else DEFAULT_EVENTS
    if not path.exists():
        raise SystemExit(
            f"planning macro introuvable ({path}) — générer d'abord : "
            "python -m engine.macro.generate"
        )
    all_events = events_from_records(json.loads(path.read_text(encoding="utf-8")))
    if not all_events:
        raise SystemExit("planning macro vide ou invalide")

    studies: dict[tuple[str, str, str], dict] = {}
    async with httpx.AsyncClient() as client:
        for symbol in symbols:
            for timeframe in timeframes:
                candles = await load_history(client, symbol, timeframe, args.days)
                if not candles:
                    logger.warning("aucune bougie %s %s", symbol, timeframe)
                    continue
                debut = datetime.fromtimestamp(candles[0].open_time / 1000, tz=timezone.utc)
                fin = datetime.fromtimestamp(candles[-1].open_time / 1000, tz=timezone.utc)
                print(
                    f"=== {symbol} {timeframe}m | {len(candles)} bougies | "
                    f"{debut:%Y-%m-%d} -> {fin:%Y-%m-%d} UTC ==="
                )
                for event_type in types:
                    study = study_type(candles, all_events, event_type)
                    studies[(symbol, timeframe, event_type)] = study
                    core = study["core"]
                    print(
                        f"{event_type:<5} évts={study['n_events']:<3}"
                        f" | pre {_fmt(study['pre']['ratio_range'])}"
                        f" | CORE {_fmt(core['ratio_range']):<7}"
                        f"(corps {_fmt(core['ratio_body'])}, vol {_fmt(core['ratio_volume'])},"
                        f" {core['n_candles']} bougies / base {core['n_baseline']})"
                        f" | post {_fmt(study['post']['ratio_range'])}"
                    )
                print()

    ok, details = gate_pass(studies)
    print("--- GATE (scellé MACRO.md §8) ---")
    for line in details:
        print(line)
    print(f"=> GATE {'PASS' if ok else 'FAIL'}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(
        description="Event study macro : volatilité autour des événements (aucun envoi)."
    )
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument("--timeframes", default="15,60")
    parser.add_argument("--days", type=int, default=1490)
    parser.add_argument("--events", default=None, help="chemin du planning JSON")
    parser.add_argument("--types", default=",".join(DEFAULT_TYPES))
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
