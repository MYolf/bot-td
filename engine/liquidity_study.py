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
import bisect
import dataclasses
import logging
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from engine.binance_client import INTERVAL_MS
from engine.liquidity.engine import (
    SEALED_CONFIGS,
    LiquiditySimulator,
    SimConfig,
    SimParams,
    TradeResult,
)
from engine.liquidity.primitives import (
    CONSUME_ATR,
    H4,
    SWEEP_BUFFER_ATR,
    atr14_4h,
    ema200_4h,
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


# ============================================================== STADE IS ==
#
# Métriques §9 et gates §10.2 (1-5, 9) pour les configurations scellées,
# entrées dans l'IS uniquement (les positions encore ouvertes à la fin de
# l'IS sortent en EOD au dernier close — mark-to-market honnête, comptées
# et signalées).

# Frais §3.4 en % du prix : aller + retour + 2 x slippage.
FEE_TAKER_PCT = 0.05 + 0.05 + 2 * 0.02  # 0.14 %
FEE_MAKER_PCT = 0.02 + 0.02 + 2 * 0.02  # 0.08 %
FEE_MAKER_IN_TAKER_OUT_PCT = 0.02 + 0.05 + 2 * 0.02  # 0.11 %

DIRECTIONS = ("long", "short")


def trade_net(trade: TradeResult, level: str) -> float:
    """R net d'un trade au niveau de frais demandé.

    level : "taker" (tout taker), "maker" (tout maker) ou "real"
    (maker à l'entrée si fill limite, taker à la sortie ; taker sinon).
    """
    if level == "taker":
        fee_pct = FEE_TAKER_PCT
    elif level == "maker":
        fee_pct = FEE_MAKER_PCT
    else:
        fee_pct = (
            FEE_MAKER_IN_TAKER_OUT_PCT
            if trade.entry_type == "maker"
            else FEE_TAKER_PCT
        )
    return trade.result_r - fee_pct / trade.risk_pct


def trade_nets(trades: list[TradeResult], level: str) -> list[float]:
    return [trade_net(t, level) for t in trades]


def summarize(nets: list[float]) -> dict:
    """n, expectancy, médiane, winrate, profit factor, max drawdown."""
    n = len(nets)
    if n == 0:
        return {"n": 0, "exp": None, "med": None, "wr": None, "pf": None, "dd": None}
    gross_win = sum(r for r in nets if r > 0)
    gross_loss = sum(r for r in nets if r < 0)
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for r in nets:
        equity += r
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return {
        "n": n,
        "exp": sum(nets) / n,
        "med": statistics.median(nets),
        "wr": 100.0 * sum(1 for r in nets if r > 0) / n,
        "pf": None if gross_loss == 0 else gross_win / abs(gross_loss),
        "dd": dd,
    }


def bootstrap_ci(nets: list[float], n_boot: int = 10_000, seed: int = 42) -> tuple[float, float] | None:
    """IC 90 % de l'expectancy par rééchantillonnage (informatif, §9)."""
    if not nets:
        return None
    rng = random.Random(seed)
    n = len(nets)
    means = sorted(
        sum(rng.choices(nets, k=n)) / n for _ in range(n_boot)
    )
    return means[int(0.05 * n_boot)], means[int(0.95 * n_boot)]


def breakout_events(candles: list[Candle], symbol: str) -> list[SweepEventStudy]:
    """Population-mère de C : breakouts §8 (clôture + EMA200 + consommation),
    même structure que sweep_events."""
    atrs = atr14_4h(candles)
    pools = pool_series(candles)
    ranges = range_series(candles, pools, atrs)
    ema = ema200_4h(candles)
    consumed: dict[str, float | None] = {"SSL": None, "BSL": None}
    events: list[SweepEventStudy] = []
    for i in range(1, len(candles)):
        a = atrs[i]
        rng = ranges[i - 1]
        if a is None or rng is None or ema[i] is None or ema[i - 1] is None:
            continue
        p = pools[i - 1]
        candle = candles[i]
        for side, direction, pool in (("BSL", "long", p.bsl), ("SSL", "short", p.ssl)):
            if pool is None:
                continue
            prev = consumed[side]
            if prev is not None and abs(pool - prev) <= CONSUME_ATR * a:
                continue
            if direction == "long":
                broke = candle.close > pool
                context = ema[i] > ema[i - 1] and candle.close > ema[i]
            else:
                broke = candle.close < pool
                context = ema[i] < ema[i - 1] and candle.close < ema[i]
            if broke and context:
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


def mother_events(
    candidate: str, candles: list[Candle], symbol: str
) -> list[SweepEventStudy]:
    if candidate in ("A1", "A2", "B"):
        return sweep_events(candles, symbol)
    return breakout_events(candles, symbol)


# ------------------------------------------------------------------ gates --


def evaluate_gates_1_5(
    trades_by_cell: dict[tuple[str, str], list[TradeResult]],
) -> dict[str, tuple[bool, list[str]]]:
    """Gates §10.2 n° 1-5, évalués PAR DIRECTION (gate 10 : chaque direction
    doit passer seule). trades_by_cell : (symbole, direction) -> trades IS.

    Retour : direction -> (passe, détails)."""
    out: dict[str, tuple[bool, list[str]]] = {}
    for direction in DIRECTIONS:
        cells = {
            symbol: trades
            for (symbol, d), trades in trades_by_cell.items()
            if d == direction and trades
        }
        details: list[str] = []
        if not cells:
            out[direction] = (False, [f"[{direction}] aucun trade"])
            continue
        all_trades = [t for trades in cells.values() for t in trades]
        brut = summarize([t.result_r for t in all_trades])
        nets_taker = trade_nets(all_trades, "taker")
        net = summarize(nets_taker)
        # 1. effectifs
        g1 = all(summarize([t.result_r for t in ts])["n"] >= 60 for ts in cells.values()) and len(all_trades) >= 80
        details.append(
            f"[{direction}] 1 effectifs : "
            + ", ".join(f"{s} n={len(ts)}" for s, ts in sorted(cells.items()))
            + f" poolé n={len(all_trades)} (>=60/cellule, >=80 poolé) -> "
            + ("OK" if g1 else "NON")
        )
        # 2. expectancy brute > 0 sur chaque symbole
        g2 = all(
            summarize([t.result_r for t in ts])["n"] > 0
            and summarize([t.result_r for t in ts])["exp"] > 0
            for ts in cells.values()
        )
        exp_txt = ", ".join(
            f"{s} {summarize([t.result_r for t in ts])['exp']:+.3f}R"
            for s, ts in sorted(cells.items())
        )
        details.append(f"[{direction}] 2 brut > 0 chaque symbole : {exp_txt} -> " + ("OK" if g2 else "NON"))
        # 3. nette taker >= +0.10R poolée, >= 0 chaque cellule
        g3 = net["exp"] is not None and net["exp"] >= 0.10 and all(
            (lambda m: m["n"] > 0 and m["exp"] >= 0)(summarize(trade_nets(ts, "taker")))
            for ts in cells.values()
        )
        details.append(
            f"[{direction}] 3 nette taker poolée {net['exp']:+.3f}R (>= +0.10) et "
            f">= 0 par cellule -> " + ("OK" if g3 else "NON")
        )
        # 4. PF net taker poolé >= 1.30
        g4 = net["pf"] is not None and net["pf"] >= 1.30
        pf_txt = f"{net['pf']:.2f}" if net["pf"] is not None else "n/a"
        details.append(f"[{direction}] 4 PF net taker poolé {pf_txt} (>= 1.30) -> " + ("OK" if g4 else "NON"))
        # 5. max DD net <= 12R poolé
        g5 = net["dd"] is not None and net["dd"] <= 12.0
        details.append(f"[{direction}] 5 max DD net taker poolé {net['dd']:.2f}R (<= 12) -> " + ("OK" if g5 else "NON"))
        out[direction] = (g1 and g2 and g3 and g4 and g5, details)
    return out


# ---------------------------------------------------------------- plateau --


def plateau_variations() -> list[tuple[str, SimParams]]:
    """±20 % de chaque paramètre libre §11 (k=3 exclu), un paramètre à la
    fois, les autres à leur valeur scellée. Entiers : arrondi."""
    out: list[tuple[str, SimParams]] = []
    for f in dataclasses.fields(SimParams):
        base = f.default
        for factor, tag in ((0.8, "-20%"), (1.2, "+20%")):
            value = base * factor
            if f.type == "int":
                value = round(value)
            out.append((f"{f.name} {tag}", SimParams(**{f.name: value})))
    return out


# ---------------------------------------------------- regimes et ventilations


def regime_at(
    candles_4h: list[Candle], ema: list[float | None], atrs: list[float | None],
    close_times: list[int], entry_ms: int, vol_median: float | None,
) -> str:
    """Régime §3.5 à l'entrée d'un trade : bull/bear/chop + HIGH/LOW VOL."""
    i = bisect.bisect_right(close_times, entry_ms) - 1
    if i < 20 or ema[i] is None or ema[i - 1] is None:
        return "chop"
    bull = ema[i] > ema[i - 1] and candles_4h[i].close > ema[i]
    for j in range(i - 19, i + 1):
        if ema[j] is None or ema[j - 1] is None or not ema[j] > ema[j - 1]:
            bull = False
            break
    bear = ema[i] < ema[i - 1] and candles_4h[i].close < ema[i]
    if bear:
        for j in range(i - 19, i + 1):
            if ema[j] is None or ema[j - 1] is None or not ema[j] < ema[j - 1]:
                bear = False
                break
    trend = "bull" if bull else ("bear" if bear else "chop")
    if vol_median is not None and atrs[i] is not None:
        vol = "HIGHVOL" if atrs[i] / candles_4h[i].close > vol_median else "LOWVOL"
        return f"{trend}/{vol}"
    return trend


def ventilation(trades: list[TradeResult], label_of) -> dict[str, float]:
    """Expectancy nette taker ventilée par bucket (année, régime...)."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        buckets[label_of(t)].append(trade_net(t, "taker"))
    return {k: sum(v) / len(v) for k, v in sorted(buckets.items())}


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


async def _load_is_1h(
    client: httpx.AsyncClient, symbol: str, days: int
) -> list[Candle]:
    """Bougies 1H tronquées à la fin de l'IS."""
    candles = await load_history(client, symbol, "60", days)
    end_ms = _to_ms(IS_END)
    return [c for c in candles if c.open_time + INTERVAL_MS["60"] <= end_ms]


def _fmt_r(v: float | None) -> str:
    return f"{v:+.3f}R" if v is not None else "n/a"


def _fmt_f(v: float | None) -> str:
    return f"{v:.2f}" if v is not None else "n/a"


def _cell_line(label: str, trades: list[TradeResult]) -> str:
    brut = summarize([t.result_r for t in trades])
    net_t = summarize(trade_nets(trades, "taker"))
    net_m = summarize(trade_nets(trades, "maker"))
    net_r = summarize(trade_nets(trades, "real"))
    eod = sum(1 for t in trades if t.exit_reason == "EOD")
    dur = sum(t.duration_h for t in trades) / len(trades)
    return (
        f"  {label:<18} n={brut['n']:<4} brut {_fmt_r(brut['exp'])} | "
        f"taker {_fmt_r(net_t['exp'])} PF {_fmt_f(net_t['pf'])} DD {net_t['dd']:.2f}R | "
        f"maker {_fmt_r(net_m['exp'])} | maker-in/taker-out {_fmt_r(net_r['exp'])} | "
        f"WR {brut['wr']:.0f}% | med {_fmt_r(brut['med'])} | dur {dur:.0f}h | EOD {eod}"
    )


async def _stage_is(client: httpx.AsyncClient, args: argparse.Namespace) -> None:
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    data: dict[str, tuple[list[Candle], list[Candle]]] = {}
    regimes: dict[str, tuple] = {}
    for symbol in symbols:
        c4 = await _load_is_4h(client, symbol, args.days)
        c1 = await _load_is_1h(client, symbol, args.days)
        if not c4 or not c1:
            raise SystemExit(f"bougies manquantes pour {symbol}")
        data[symbol] = (c4, c1)
        atrs4 = atr14_4h(c4)
        ema = ema200_4h(c4)
        start_ms = _to_ms(IS_START)
        ratios = [
            a / c.close
            for c, a in zip(c4, atrs4)
            if a is not None and c.open_time + H4 >= start_ms
        ]
        regimes[symbol] = (
            ema,
            atrs4,
            [c.open_time + H4 for c in c4],
            statistics.median(ratios) if ratios else None,
        )
        print(
            f"données {symbol} : {len(c4)} bougies 4H / {len(c1)} bougies 1H "
            f"({datetime.fromtimestamp(c4[0].open_time / 1000, tz=timezone.utc):%Y-%m-%d} "
            f"-> {datetime.fromtimestamp(c4[-1].open_time / 1000, tz=timezone.utc):%Y-%m-%d})"
        )

    start_ms = _to_ms(IS_START)
    end_ms = _to_ms(IS_END)
    journal: list[str] = []
    for cand, pol in SEALED_CONFIGS:
        config = SimConfig(cand, pol)
        print()
        print(f"===== {cand}/{pol} =====")
        trades_by_cell: dict[tuple[str, str], list[TradeResult]] = {}
        pooled: list[TradeResult] = []
        for symbol, (c4, c1) in data.items():
            sim = LiquiditySimulator(c4, c1, config)
            trades = [t for t in sim.run() if start_ms <= t.entry_time_ms < end_ms]
            mothers = [
                e
                for e in mother_events(cand, c4, symbol)
                if start_ms <= e.close_ms < end_ms
            ]
            for direction in DIRECTIONS:
                ts = [t for t in trades if t.direction == direction]
                trades_by_cell[(symbol, direction)] = ts
                if ts:
                    print(_cell_line(f"{symbol} {direction}", ts))
            pooled.extend(trades)
            conv = 100.0 * len(trades) / len(mothers) if mothers else 0.0
            fills = sum(1 for t in trades if t.entry_type == "maker")
            print(
                f"  -> {symbol} : {len(mothers)} événements-mères, {len(trades)} trades "
                f"(conversion {conv:.0f} %), fills maker {fills}"
            )
            ema_s, atrs_s, close_times_s, vol_med_s = regimes[symbol]
            vent_regime = ventilation(
                trades,
                lambda t: regime_at(
                    c4, ema_s, atrs_s, close_times_s, t.entry_time_ms, vol_med_s
                ),
            )
            if vent_regime:
                print(
                    f"  régimes {symbol} (net taker) : "
                    + ", ".join(f"{k}: {v:+.2f}R" for k, v in vent_regime.items())
                )
        if not pooled:
            print("  AUCUN TRADE — configuration non évaluable")
            journal.append(f"IS | {cand}/{pol} | aucun trade | REJET")
            continue
        print(_cell_line("POOLÉ (2 symboles)", pooled))
        pooled_nets = trade_nets(pooled, "taker")
        ci = bootstrap_ci(pooled_nets)
        if ci:
            print(f"  IC 90 % expectancy nette taker poolée : [{ci[0]:+.3f}R, {ci[1]:+.3f}R]")
        vent_annee = ventilation(
            pooled,
            lambda t: str(
                datetime.fromtimestamp(t.entry_time_ms / 1000, tz=timezone.utc).year
            ),
        )
        print(
            "  par année (net taker) : "
            + ", ".join(f"{k}: {v:+.2f}R" for k, v in vent_annee.items())
        )

        print("  --- Gates 1-5 (par direction, évaluée seule — gate 10) ---")
        gates = evaluate_gates_1_5(trades_by_cell)
        for direction in DIRECTIONS:
            ok, details = gates[direction]
            for line in details:
                print(f"  {line}")
            print(f"  => {cand}/{pol} [{direction}] : gates 1-5 {'PASS' if ok else 'FAIL'}")

        plateau_ok, plateau_worst = True, None
        if args.plateau:
            print("  --- Gate 9 : plateau ±20 % (expectancy nette taker poolée) ---")
            for label, params in plateau_variations():
                nets: list[float] = []
                for symbol, (c4, c1) in data.items():
                    sim = LiquiditySimulator(c4, c1, config, params)
                    nets.extend(
                        trade_net(t, "taker")
                        for t in sim.run()
                        if start_ms <= t.entry_time_ms < end_ms
                    )
                exp = sum(nets) / len(nets) if nets else None
                if plateau_worst is None or (exp is not None and plateau_worst[1] is not None and exp < plateau_worst[1]):
                    plateau_worst = (label, exp)
                ok_var = exp is not None and exp > 0
                plateau_ok = plateau_ok and ok_var
                print(
                    f"  plateau {label:<28} n={len(nets):<4} exp {_fmt_r(exp)} "
                    f"{'OK' if ok_var else 'NON'}"
                )
            print(
                f"  => plateau {'PASS' if plateau_ok else 'FAIL'} "
                f"(pire : {plateau_worst[0]} {_fmt_r(plateau_worst[1])})"
            )

        resume = " ; ".join(
            f"{d}: {'PASS' if gates[d][0] else 'FAIL'}" for d in DIRECTIONS
        )
        resume += f" ; plateau: {'PASS' if plateau_ok else 'FAIL'}"
        with TRIALS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(
                f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC | IS | "
                f"{cand}/{pol} | poolé n={len(pooled)} "
                f"net taker {_fmt_r(summarize(pooled_nets)['exp'])} | gates 1-5+9 : {resume}\n"
            )
        journal.append(f"IS | {cand}/{pol} | {resume}")

    print()
    print("--- Synthèse IS (gates 1-5 + 9 ; gates 6-8 = OOS/WF, plus tard) ---")
    for line in journal:
        print(f"  {line}")
    print(f"(journalisé dans {TRIALS_LOG})")


async def _run(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient() as client:
        if args.stage == "gate0":
            await _stage_gate0(client, args)
        elif args.stage == "is":
            await _stage_is(client, args)
        else:
            raise SystemExit(f"stade inconnu : {args.stage} (étapes OOS/WF à venir)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(
        description="Étude LIQUIDITY (aucun envoi réseau sortant hors Binance données publiques)."
    )
    parser.add_argument("--stage", default="gate0", choices=["gate0", "is"])
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument(
        "--no-plateau",
        dest="plateau",
        action="store_false",
        help="stade is : sauter le gate 9 (plateau +/-20 pourcents)",
    )
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
