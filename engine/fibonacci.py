"""Retracement Fibonacci — module de MESURE (Phase 32, FIBONACCI.md scellé).

Le Fibonacci est une mesure de confluence, JAMAIS un déclencheur : ce module
n'est utilisé ni par les gâchettes, ni par le score, ni par la production tant
que l'hypothèse de FIBONACCI.md §5 n'est pas validée out-of-sample.

Définitions (scellées avant toute mesure sur les données) :

- **Impulsion** : leg entre deux swings fractals confirmés consécutifs de sens
  opposés, d'amplitude >= ``min_amplitude_atr`` x ATR, de durée <=
  ``max_leg_bars``, contenant au moins un displacement et un BOS du même sens.
- **Niveaux** : impulsion haussière ``r`` = ``high - r x (high - low)`` (0 au
  sommet, 1 à l'origine — convention TradingView) ; miroir exact en baissier.
- **Bande utile** : [niveau 0.50, niveau 0.70] (le 0.70 est une borne, pas un
  niveau de retracement).
- **Cycle de vie** : création à la confirmation du second swing (``i + k``) ;
  unicité (toute nouvelle impulsion qualifiée remplace l'active, même en cours
  de retracement) ; 3e retest = consommé ; clôture au-delà du niveau 1.0 =
  invalidé ; expiration ``expiry_bars`` bougies après la création. Un retest
  est compté par ÉPISODE de contact (le prix doit sortir de la bande entre
  deux retests).

Contrat anti-lookahead (propriété de préfixe, testée en unitaire) :
``fib_states(candles[:t+1]) == fib_states(candles)[:t+1]``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from engine.indicators import atr as atr_series
from engine.strategy import Candle
from engine.structure import BOS_BEARISH, BOS_BULLISH, find_swings, market_structure
from engine.zones import displacements

BULLISH = "bullish"
BEARISH = "bearish"

ACTIVE = "active"
INVALIDATED = "invalidated"
EXPIRED = "expired"
CONSUMED = "consumed"

# Niveaux calculés (les trois premiers servent à la visualisation/bucketisation).
LEVELS: tuple[float, ...] = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)

DEFAULT_DUMP_DIR = Path("data/fib_dump")


@dataclass(frozen=True)
class FibParams:
    """Seuils scellés par FIBONACCI.md §2-4 (ne pas retoucher après mesure)."""

    swing_k: int = 3
    atr_len: int = 14
    min_amplitude_atr: float = 4.0
    max_leg_bars: int = 60
    zone_low_r: float = 0.50
    zone_high_r: float = 0.70
    expiry_bars: int = 200
    max_retests: int = 3
    disp_mult: float = 1.5
    disp_max_bars: int = 3
    proximity_atr: float = 0.5


@dataclass(frozen=True)
class FibImpulse:
    """Leg validé entre deux swings consécutifs de sens opposés."""

    direction: str  # bullish / bearish
    start_index: int  # origine du leg : swing low (haussier) / swing high (baissier)
    end_index: int  # extrême du leg : swing high (haussier) / swing low (baissier)
    confirmed_at: int  # bougie de création du Fib (confirmation du second swing)
    swing_low: float
    swing_high: float

    @property
    def span(self) -> float:
        return self.swing_high - self.swing_low

    def level(self, r: float) -> float:
        """Niveau de retracement ``r`` (0 = extrême, 1 = origine, comme TradingView)."""
        if self.direction == BULLISH:
            return self.swing_high - r * self.span
        return self.swing_low + r * self.span


@dataclass(frozen=True)
class FibState:
    """Instantané du Fib actif à une bougie (après traitement de la bougie)."""

    index: int
    status: str  # active / invalidated / expired / consumed
    impulse: FibImpulse
    retests: int
    in_band: bool  # la bougie courante est dans un épisode de contact
    zone_bottom: float  # borne basse de la bande 0.50-0.70
    zone_top: float  # borne haute de la bande 0.50-0.70
    depth: float | None  # retracement r du close (peut sortir de [0, 1])


# ------------------------------------------------------------- impulsion ---


def _candidates(swings: list, params: FibParams) -> list[FibImpulse]:
    """Paires de swings consécutifs de sens opposés (low->high ou high->low)."""
    out: list[FibImpulse] = []
    for prev, cur in zip(swings, swings[1:]):
        if prev.kind == cur.kind:
            continue
        if prev.kind == "low":  # swing low puis swing high : impulsion haussière
            out.append(
                FibImpulse(
                    BULLISH, prev.index, cur.index, cur.confirmed_at, prev.price, cur.price
                )
            )
        else:  # swing high puis swing low : impulsion baissière
            out.append(
                FibImpulse(
                    BEARISH, prev.index, cur.index, cur.confirmed_at, cur.price, prev.price
                )
            )
    out.sort(key=lambda c: c.confirmed_at)
    return out


def _qualified(
    impulse: FibImpulse,
    atrs: list[float | None],
    structure_events: list,
    disp_events: list,
    params: FibParams,
) -> bool:
    """Conditions scellées FIBONACCI.md §2, évaluées à la bougie de confirmation."""
    atr = atrs[impulse.confirmed_at]
    if atr is None or atr <= 0.0:
        return False
    if impulse.swing_high - impulse.swing_low < params.min_amplitude_atr * atr:
        return False  # amplitude insuffisante
    if impulse.end_index - impulse.start_index > params.max_leg_bars:
        return False  # leg trop long
    want_bos = BOS_BULLISH if impulse.direction == BULLISH else BOS_BEARISH
    if not any(
        event.kind == want_bos
        and impulse.start_index <= event.index <= impulse.end_index
        for event in structure_events
    ):
        return False  # pas de BOS validant le leg
    return any(
        event.direction == impulse.direction
        and impulse.start_index <= event.index <= impulse.end_index
        for event in disp_events
    )  # pas de displacement -> pas d'impulsion


# ------------------------------------------------------------------ états ---


def fib_states(
    candles: list[Candle], params: FibParams | None = None
) -> list[FibState | None]:
    """Instantané du Fib par bougie (``None`` = aucun Fib actif).

    Une seule impulsion est active à la fois ; toute nouvelle impulsion
    qualifiée la remplace immédiatement. Les barres sont traitées dans
    l'ordre : remplacement, puis expiration, puis invalidation, puis comptage
    des retests (la bougie de création est elle-même traitée).
    """
    params = params or FibParams()
    n = len(candles)
    out: list[FibState | None] = [None] * n
    if n == 0:
        return out

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    atrs = atr_series(highs, lows, closes, params.atr_len)
    structure = market_structure(candles, params.swing_k)
    disp_events = displacements(
        candles, params.atr_len, params.disp_mult, params.disp_max_bars
    )
    swings = find_swings(candles, params.swing_k)
    candidates = _candidates(swings, params)

    ci = 0
    active: FibImpulse | None = None
    retests = 0
    in_band = False

    for i in range(n):
        # Création / remplacement : candidats confirmés à la bougie i.
        while ci < len(candidates) and candidates[ci].confirmed_at == i:
            if _qualified(candidates[ci], atrs, structure.events, disp_events, params):
                active = candidates[ci]
                retests = 0
                in_band = False
            ci += 1
        if active is None:
            continue

        impulse = active
        zone_low_level = impulse.level(params.zone_low_r)
        zone_high_level = impulse.level(params.zone_high_r)
        zone_bottom = min(zone_low_level, zone_high_level)
        zone_top = max(zone_low_level, zone_high_level)
        status = ACTIVE
        close = closes[i]
        if impulse.direction == BULLISH:
            depth = (impulse.swing_high - close) / impulse.span
        else:
            depth = (close - impulse.swing_low) / impulse.span

        if i - impulse.confirmed_at > params.expiry_bars:
            status = EXPIRED
        elif (impulse.direction == BULLISH and close < impulse.swing_low) or (
            impulse.direction == BEARISH and close > impulse.swing_high
        ):
            status = INVALIDATED  # clôture au-delà du niveau 1.0
        else:
            touching = lows[i] <= zone_top and highs[i] >= zone_bottom
            if touching and not in_band:
                retests += 1  # nouvel épisode de contact
                in_band = True
                if retests >= params.max_retests:
                    status = CONSUMED
            elif not touching:
                in_band = False  # fin de l'épisode courant

        out[i] = FibState(
            index=i,
            status=status,
            impulse=impulse,
            retests=retests,
            in_band=in_band,
            zone_bottom=zone_bottom,
            zone_top=zone_top,
            depth=depth,
        )
        if status != ACTIVE:
            active = None
            retests = 0
            in_band = False

    return out


# ------------------------------------------------------- overlap OB x bande --


def ob_fib_grade(
    ob_direction: str,
    ob_bottom: float,
    ob_top: float,
    state: FibState | None,
    atr: float | None,
    proximity_atr: float = FibParams.proximity_atr,
) -> str:
    """Grade de chevauchement Order Block x bande 0.50-0.70 (FIBONACCI.md §4).

    ``inclusion`` (OB dans la bande), ``overlap`` (intersection sans inclusion),
    ``proximity`` (disjoint à <= ``proximity_atr`` x ATR), sinon ``none``.
    Les directions doivent correspondre ; un Fib non actif ne grade jamais.
    """
    if (
        state is None
        or state.status != ACTIVE
        or atr is None
        or atr <= 0.0
        or ob_direction != state.impulse.direction
    ):
        return "none"
    if ob_bottom >= state.zone_bottom and ob_top <= state.zone_top:
        return "inclusion"
    if ob_top >= state.zone_bottom and ob_bottom <= state.zone_top:
        return "overlap"
    gap = max(state.zone_bottom - ob_top, ob_bottom - state.zone_top)
    if 0.0 < gap <= proximity_atr * atr:
        return "proximity"
    return "none"


# ------------------------------------------------------------------- dump ---


def _ms(index: int, candles: list[Candle]) -> tuple[int, int]:
    candle = candles[index]
    return candle.open_time, candle.close_time


def build_dump(
    candles: list[Candle],
    states: list[FibState | None],
    blocks: list,
    gaps: list,
) -> dict:
    """Payload JSON de visualisation (comparaison avec TradingView)."""
    impulses: dict[tuple[int, ...], dict] = {}
    bars: list[dict] = []
    for i, state in enumerate(states):
        if state is None:
            continue
        imp = state.impulse
        key = (imp.confirmed_at, imp.direction, imp.start_index, imp.end_index)
        if key not in impulses:
            start_time, _ = _ms(imp.start_index, candles)
            end_time, _ = _ms(imp.end_index, candles)
            impulses[key] = {
                "direction": imp.direction,
                "start_index": imp.start_index,
                "start_time": start_time,
                "end_index": imp.end_index,
                "end_time": end_time,
                "confirmed_at": imp.confirmed_at,
                "swing_low": imp.swing_low,
                "swing_high": imp.swing_high,
                "levels": {f"{r}": imp.level(r) for r in LEVELS},
                "zone": [imp.level(0.50), imp.level(0.70)],
            }
        bars.append(
            {
                "index": i,
                "open_time": candles[i].open_time,
                "status": state.status,
                "retests": state.retests,
                "in_band": state.in_band,
                "zone_bottom": round(state.zone_bottom, 10),
                "zone_top": round(state.zone_top, 10),
                "depth": None if state.depth is None else round(state.depth, 6),
            }
        )
    return {
        "n_candles": len(candles),
        "first_time": candles[0].open_time if candles else None,
        "last_time": candles[-1].open_time if candles else None,
        "impulses": list(impulses.values()),
        "bars": bars,
        "order_blocks": [
            {
                "index": b.index,
                "open_time": candles[b.index].open_time,
                "direction": b.direction,
                "bottom": b.bottom,
                "top": b.top,
                "bos_index": b.bos_index,
                "first_retest_index": b.first_retest_index,
                "first_mitigation_index": b.first_mitigation_index,
                "invalidated_at": b.invalidated_at,
                "expired": b.expired,
            }
            for b in blocks
        ],
        "fair_value_gaps": [
            {
                "index": g.index,
                "open_time": candles[g.index].open_time,
                "direction": g.direction,
                "bottom": g.bottom,
                "top": g.top,
                "first_retest_index": g.first_retest_index,
                "invalidated_at": g.invalidated_at,
                "expired": g.expired,
            }
            for g in gaps
        ],
    }


def _fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


async def _run(args: argparse.Namespace) -> None:
    # Import local : évite de charger la pile HTTP au simple import du module.
    from engine.validation import load_history
    from engine.zones import fair_value_gaps, order_blocks

    import httpx

    async with httpx.AsyncClient() as client:
        candles = await load_history(client, args.symbol, args.timeframe, args.days)
    if not candles:
        raise SystemExit("aucune bougie récupérée")
    params = FibParams()
    states = fib_states(candles, params)
    blocks = order_blocks(candles, swing_k=params.swing_k, atr_len=params.atr_len)
    gaps = fair_value_gaps(candles, atr_len=params.atr_len)

    created = {
        (s.impulse.confirmed_at, s.impulse.direction) for s in states if s is not None
    }
    terminals: dict[str, int] = {}
    for s in states:
        if s is not None and s.status != ACTIVE:
            terminals[s.status] = terminals.get(s.status, 0) + 1
    still_active = any(s is not None and s.status == ACTIVE for s in reversed(states))
    detail = ", ".join(f"{k}={v}" for k, v in sorted(terminals.items())) or "aucun"
    print(
        f"=== {args.symbol} {args.timeframe}m | {len(candles)} bougies | "
        f"{_fmt(candles[0].open_time)} -> {_fmt(candles[-1].open_time)} UTC ==="
    )
    print(
        f"impulsions Fib qualifiées : {len(created)} | terminaisons : {detail}"
        f"{' | 1 encore active' if still_active else ''}"
    )

    if args.dump:
        payload = build_dump(candles, states, blocks, gaps)
        DEFAULT_DUMP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = DEFAULT_DUMP_DIR / f"{args.symbol}_{args.timeframe}m_{args.days}d_{stamp}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"dump JSON : {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mesure Fibonacci (aucun envoi, aucune décision de trading)."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="15", choices=("15", "60"))
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument(
        "--dump", action="store_true", help="écrit un JSON de visualisation"
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
