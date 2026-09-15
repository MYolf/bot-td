"""Étude LIQUIDITY — étapes §13 : GATE 0 (event-study IS), puis IS/OOS/WF.

Stade ``gate0`` (LIQUIDITY.md §10.1) :
- population-mère A/B = sweeps §4.4 (range valide à t_s-1 + pénétration +
  rejet), anti-re-sweep appliqué au niveau ÉVÉNEMENT (consommation à la
  détection, comme le moteur de simulation) ;
- forward returns sens-ajustés à h = 12, 24, 48 bougies 4H après chaque
  sweep (LONG : +close[t+h]/close[t]-1 ; SHORT : l'opposé) ;
- baseline appariée méthode macro_study : bougies 4H de même (jour de
  semaine, position dans le jour = heure d'ouverture), sur des jours UTC
  SANS aucun événement. Interprétation documentée : la baseline est la
  médiane des forward returns BRUTS (pas de sens hors événement) ;
- fenêtre IS UNIQUEMENT : les bougies sont tronquées à 2025-03-01 UTC avant
  tout calcul — l'OOS n'est pas consulté à ce stade. Un événement dont
  t_s + h sortirait des données est ignoré pour l'horizon h.

GATE scellé : n >= 100 événements IS poolés (BTC+ETH) ET médiane du forward
return sens-ajusté > 0 ET > baseline sur au moins un horizon. Échec -> A et B
rejetés sans consommer l'OOS ; C continue.

Journal d'essais : data/liquidity/trials.log (append, non commité).

Usage :
    python -m engine.liquidity_study --stage gate0
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from engine.binance_client import INTERVAL_MS
from engine.liquidity.primitives import (
    CONSUME_ATR,
    H4,
    SWEEP_BUFFER_ATR,
    atr14_4h,
    pool_series,
    range_series,
)
from engine.strategy import Candle
from engine.validation import load_history

logger = logging.getLogger(__name__)

# Fenêtres scellées (LIQUIDITY.md §3.2).
IS_START = datetime(2022, 1, 1, tzinfo=timezone.utc)
IS_END = datetime(2025, 3, 1, tzinfo=timezone.utc)

HORIZONS_4H = (12, 24, 48)
GATE_MIN_EVENTS = 100

TRIALS_LOG = Path("data/liquidity/trials.log")


# ------------------------------------------------------------- événements --


@dataclass(frozen=True)
class SweepEventStudy:
    """Un sweep §4.4 (population-mère A/B), détecté à la clôture 4H t_s."""

    index: int  # bougie 4H de l'événement
    symbol: str
    side: str  # "SSL" (cible LONG) | "BSL" (cible SHORT)
    direction: str  # "long" | "short"
    pool: float
    close_ms: int  # clôture logique de la bougie t_s


def sweep_events(candles: list[Candle], symbol: str) -> list[SweepEventStudy]:
    """Tous les sweeps §4.4 (propriété de préfixe), anti-re-sweep inclus.

    La consommation a lieu à la DÉTECTION (le pool a été pris) : deux bougies
    consécutives perçant le même niveau ne produisent qu'UN événement, comme
    dans le moteur de simulation.
    """
    atrs = atr14_4h(candles)
    pools = pool_series(candles)
    ranges = range_series(candles, pools, atrs)
    consumed: dict[str, float | None] = {"SSL": None, "BSL": None}
    events: list[SweepEventStudy] = []
    for i in range(1, len(candles)):
        a = atrs[i]
        rng = ranges[i - 1]
        if a is None or rng is None:
            continue
        p = pools[i - 1]
        candle = candles[i]
        for side, direction, pool in (("SSL", "long", p.ssl), ("BSL", "short", p.bsl)):
            if pool is None:
                continue
            prev = consumed[side]
            if prev is not None and abs(pool - prev) <= CONSUME_ATR * a:
                continue
            if direction == "long":
                pierced = candle.low <= pool - SWEEP_BUFFER_ATR * a
                rejected = candle.close > pool
            else:
                pierced = candle.high >= pool + SWEEP_BUFFER_ATR * a
                rejected = candle.close < pool
            if pierced and rejected:
                consumed[side] = pool
                events.append(
                    SweepEventStudy(
                        index=i,
                        symbol=symbol,
                        side=side,
                        direction=direction,
                        pool=pool,
                        close_ms=candle.open_time + H4,
                    )
                )
    return events


# -------------------------------------------------------- forward returns --


def forward_return_pct(candles: list[Candle], i: int, h: int) -> float | None:
    """(close[i+h] / close[i] - 1) x 100 ; None si i+h hors données."""
    j = i + h
    if j >= len(candles):
        return None
    return (candles[j].close / candles[i].close - 1.0) * 100.0


def sens_adjust(fr: float, direction: str) -> float:
    return fr if direction == "long" else -fr


def event_forward_returns(
    candles: list[Candle], events: list[SweepEventStudy]
) -> dict[int, list[float]]:
    """Forward returns sens-ajustés par horizon, pour les événements donnés."""
    out: dict[int, list[float]] = defaultdict(list)
    for event in events:
        for h in HORIZONS_4H:
            fr = forward_return_pct(candles, event.index, h)
            if fr is not None:
                out[h].append(sens_adjust(fr, event.direction))
    return out


def event_days(events: list[SweepEventStudy]) -> set[date]:
    """Jours UTC (de la bougie t_s) portant au moins un événement."""
    return {
        datetime.fromtimestamp(e.close_ms / 1000, tz=timezone.utc).date() for e in events
    }


def baseline_forward_returns(
    candles: list[Candle], events: list[SweepEventStudy]
) -> dict[int, list[float]]:
    """Forward returns BRUTS des bougies appariées : même (jour de semaine,
    heure d'ouverture 4H) que les bougies événement, sur des jours sans
    événement (méthode macro_study, adaptée aux slots 4H)."""
    by_slot: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, candle in enumerate(candles):
        moment = datetime.fromtimestamp(candle.open_time / 1000, tz=timezone.utc)
        by_slot[(moment.weekday(), moment.hour)].append(i)
    exclude = event_days(events)
    slots: set[tuple[int, int]] = set()
    for e in events:
        moment = datetime.fromtimestamp(e.close_ms / 1000 - H4 / 1000, tz=timezone.utc)
        slots.add((moment.weekday(), moment.hour))
    out: dict[int, list[float]] = defaultdict(list)
    for slot in slots:
        for i in by_slot.get(slot, ()):
            moment = datetime.fromtimestamp(candles[i].open_time / 1000, tz=timezone.utc)
            if moment.date() in exclude:
                continue
            for h in HORIZONS_4H:
                fr = forward_return_pct(candles, i, h)
                if fr is not None:
                    out[h].append(fr)
    return out


# ------------------------------------------------------------------ gate --


def gate0_pass(
    n_events: int,
    event_fr: dict[int, list[float]],
    baseline_fr: dict[int, list[float]],
    min_events: int = GATE_MIN_EVENTS,
) -> tuple[bool, list[str]]:
    """GATE 0 scellé : n >= min_events ET, sur au moins un horizon, médiane
    événement > 0 ET médiane événement > médiane baseline."""
    details: list[str] = [f"n événements IS poolés = {n_events} (seuil {min_events})"]
    if n_events < min_events:
        details.append("=> GATE 0 FAIL (effectif insuffisant)")
        return False, details
    any_horizon = False
    for h in HORIZONS_4H:
        ev = event_fr.get(h, [])
        base = baseline_fr.get(h, [])
        med_ev = statistics.median(ev) if ev else None
        med_base = statistics.median(base) if base else None
        ok_h = (
            med_ev is not None
            and med_base is not None
            and med_ev > 0.0
            and med_ev > med_base
        )
        any_horizon = any_horizon or ok_h
        ev_txt = f"{med_ev:+.3f}%" if med_ev is not None else "n/a"
        base_txt = f"{med_base:+.3f}%" if med_base is not None else "n/a"
        details.append(
            f"h={h:>2} bougies 4H : évènements {ev_txt} (n={len(ev)}) vs "
            f"baseline {base_txt} (n={len(base)}) -> "
            f"{'OK' if ok_h else 'non'}"
        )
    passed = any_horizon
    details.append(f"=> GATE 0 {'PASS' if passed else 'FAIL'}")
    return passed, details


# ------------------------------------------------------------------ CLI --


def _to_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


async def _load_is_4h(
    client: httpx.AsyncClient, symbol: str, days: int
) -> list[Candle]:
    """Bougies 4H tronquées à la fin de l'IS (l'OOS n'est jamais chargé en
    mémoire pour ce stade)."""
    candles = await load_history(client, symbol, "240", days)
    end_ms = _to_ms(IS_END)
    return [c for c in candles if c.open_time + INTERVAL_MS["240"] <= end_ms]


def _fmt_med(values: list[float]) -> str:
    return f"{statistics.median(values):+.3f}%" if values else "n/a"


async def _stage_gate0(client: httpx.AsyncClient, args: argparse.Namespace) -> None:
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    candles_by_symbol: dict[str, list[Candle]] = {}
    events_by_symbol: dict[str, list[SweepEventStudy]] = {}
    start_ms = _to_ms(IS_START)
    for symbol in symbols:
        candles = await _load_is_4h(client, symbol, args.days)
        if not candles:
            raise SystemExit(f"aucune bougie 4H pour {symbol}")
        candles_by_symbol[symbol] = candles
        events = [e for e in sweep_events(candles, symbol) if start_ms <= e.close_ms]
        events_by_symbol[symbol] = events
        debut = datetime.fromtimestamp(candles[0].open_time / 1000, tz=timezone.utc)
        fin = datetime.fromtimestamp(candles[-1].open_time / 1000, tz=timezone.utc)
        longs = sum(1 for e in events if e.direction == "long")
        print(
            f"=== {symbol} 4H | {len(candles)} bougies | {debut:%Y-%m-%d} -> "
            f"{fin:%Y-%m-%d} UTC | sweeps IS = {len(events)} "
            f"({longs} SSL/long, {len(events) - longs} BSL/short) ==="
        )

    # Rapport par symbole (diagnostique), gate sur le poolé (scellé).
    pooled_events: list[SweepEventStudy] = []
    pooled_fr: dict[int, list[float]] = defaultdict(list)
    pooled_base: dict[int, list[float]] = defaultdict(list)
    for symbol, events in events_by_symbol.items():
        candles = candles_by_symbol[symbol]
        ev_fr = event_forward_returns(candles, events)
        base_fr = baseline_forward_returns(candles, events)
        print(f"--- {symbol} (diagnostique, hors gate) ---")
        for h in HORIZONS_4H:
            print(
                f"  h={h:>2} : événements {_fmt_med(ev_fr.get(h, []))} "
                f"(n={len(ev_fr.get(h, []))}) | baseline {_fmt_med(base_fr.get(h, []))} "
                f"(n={len(base_fr.get(h, []))})"
            )
        pooled_events.extend(events)
        for h, values in ev_fr.items():
            pooled_fr[h].extend(values)
        for h, values in base_fr.items():
            pooled_base[h].extend(values)

    ok, details = gate0_pass(len(pooled_events), pooled_fr, pooled_base)
    print()
    print("--- GATE 0 (scellé LIQUIDITY.md §10.1, IS uniquement) ---")
    for line in details:
        print(line)

    TRIALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    resume = " | ".join(details[1:-1])
    with TRIALS_LOG.open("a", encoding="utf-8") as fh:
        fh.write(
            f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC | GATE0 | "
            f"fenêtre=IS {IS_START:%Y-%m-%d}->{IS_END:%Y-%m-%d} | {resume} | "
            f"{'PASS' if ok else 'FAIL'}\n"
        )
    print(f"(journalisé dans {TRIALS_LOG})")


async def _run(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient() as client:
        if args.stage == "gate0":
            await _stage_gate0(client, args)
        else:
            raise SystemExit(f"stade inconnu : {args.stage} (étapes IS/OOS/WF à venir)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(
        description="Étude LIQUIDITY (aucun envoi réseau sortant hors Binance données publiques)."
    )
    parser.add_argument("--stage", default="gate0", choices=["gate0"])
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument(
        "--days",
        type=int,
        default=(datetime.now(timezone.utc) - datetime(2021, 10, 1, tzinfo=timezone.utc)).days + 2,
        help="historique chargé depuis Binance (défaut : couvre 2021-10-01)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
