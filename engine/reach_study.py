"""Reach study — Phase A de la spec scellée ANTICIPATION.md.

Question : si le moteur annonçait le niveau P* à l'OUVERTURE de la bougie
(au lieu d'attendre que le prix approche à eps, mode réactif), quelle
fraction des annonces serait réellement TOUCHÉE, confirmée, et avec quel
avantage de remplissage ?

Pour chaque bougie i où un niveau P* existe (transition possible, score au
niveau >= min_score, pyramiding=0 respecté), on mesure :

- la distance d'atteinte ``d = |P* − open_i| / ATR14`` (ATR des bougies
  FERMÉES uniquement — anti-lookahead ANTICIPATION.md §4 : le high/low de la
  bougie courante n'entre jamais dans la décision d'annoncer) ;
- l'issue sur un horizon de N bougies (1, 2, 4, 8), sémantique exacte du
  cycle de vie runtime (spec §3) :
  * touché + signal officiel à la clôture de la bougie touchante -> CONFIRMED
  * touché sans confirmation à cette même clôture    -> CANCELLED (annulation)
  * signal officiel opposé avant toute touche         -> INVALIDATED
  * signal officiel même sens sans touche (dérive du
    niveau, l'utilisateur n'est pas rempli)           -> SIGNAL_NO_FILL
  * jamais touché à l'expiration                      -> EXPIRED
- l'avantage de fill quand confirmé, le coût de décharge quand annulé ;
- le volume d'annonces/jour avec la règle anti-spam runtime (une seule
  annonce active par direction, spec §3).

Buckets de distance scellés (spec §5) : d <= 0,25 / 0,5 / 1,0 / 1,5 / 2,0 ATR.
Fenêtres scellées : IS = 3 premières années (jours 1->1095), OOS = année
finale (jours 1096->1490), consommée UNE SEULE fois après figage de (k, N).

Usage :
    python -m engine.reach_study --symbol BTCUSDT --stage is
    python -m engine.reach_study --symbol ETHUSDT --stage is
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from dataclasses import dataclass, field

import httpx

from engine.indicators import atr, ema
from engine.position import PositionTracker
from engine.strategy import (
    Candle,
    MomentumParams,
    compute_series,
    evaluate_at,
    total_score,
)
from engine.levels import (
    FormingState,
    bearish_at,
    bullish_at,
    score_at,
    trigger_level,
    wilder_state,
)
from engine.validation import load_history

DAY_MS = 86_400_000
DEFAULT_DAYS = 1490
IS_DAYS = 1095  # scellé ANTICIPATION.md §5 (IS = jours 1->1095, les plus anciens)
HORIZONS = (1, 2, 4, 8)  # scellés
BUCKETS = (0.25, 0.5, 1.0, 1.5, 2.0)  # scellés

CONFIRMED = "confirmed"
CANCELLED = "cancelled"
INVALIDATED = "invalidated"
SIGNAL_NO_FILL = "signal_no_fill"
EXPIRED = "expired"
_TOUCHED_OUTCOMES = (CONFIRMED, CANCELLED)


@dataclass
class ReachCandidate:
    """Niveau P* announcing à l'ouverture de la bougie ``index``."""

    symbol: str
    index: int
    open_time: int
    action: str
    level: float
    d_atr: float  # |P* − open| / ATR14 des bougies fermées
    bucket: float | None  # plus petit bucket >= d_atr (None si > 2 ATR)
    fillable_pre: bool = True  # pyramiding=0 respecté au moment de l'annonce
    outcomes: dict[int, str] = field(default_factory=dict)
    resolve_idx: dict[int, int] = field(default_factory=dict)
    fill_advantage: dict[int, float | None] = field(default_factory=dict)
    bail_out: dict[int, float | None] = field(default_factory=dict)


def _bucket_for(d_atr: float) -> float | None:
    for k in BUCKETS:
        if d_atr <= k:
            return k
    return None


def run_reach_study(
    symbol: str,
    candles: list[Candle],
    params: MomentumParams | None = None,
    min_score: int = 45,
    horizons: tuple[int, ...] = HORIZONS,
) -> list[ReachCandidate]:
    """Candidats d'annonce à l'ouverture, avec issues par horizon.

    Passe principale miroir de ``touch_study.run_study`` (auto-validation
    forme fermée == moteur à chaque bougie, position simulée fidèle), mais
    le candidat est retenu dès que le niveau existe et passe le filtre —
    la distance à l'ouverture (bucket) remplace la proximité eps.
    """
    params = params or MomentumParams()
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    series = compute_series(closes, params)
    ema_fast, ema_slow = series["ema_fast"], series["ema_slow"]
    macd_line, macd_signal = series["macd"], series["macd_signal"]
    rsi_values = series["rsi"]
    ema_macd_fast = ema(closes, params.macd_fast)
    ema_macd_slow = ema(closes, params.macd_slow)
    avg_gain, avg_loss = wilder_state(closes, params.rsi_len)
    atr_values = atr(highs, lows, closes, 14)

    def engine_bullish(i: int) -> bool:
        if (
            ema_fast[i] is None
            or ema_slow[i] is None
            or macd_line[i] is None
            or macd_signal[i] is None
            or rsi_values[i] is None
        ):
            return False
        return (
            ema_fast[i] > ema_slow[i]  # type: ignore[operator]
            and rsi_values[i] > params.rsi_long_threshold  # type: ignore[operator]
            and macd_line[i] > macd_signal[i]  # type: ignore[operator]
        )

    def engine_bearish(i: int) -> bool:
        if (
            ema_fast[i] is None
            or ema_slow[i] is None
            or macd_line[i] is None
            or macd_signal[i] is None
            or rsi_values[i] is None
        ):
            return False
        return (
            ema_fast[i] < ema_slow[i]  # type: ignore[operator]
            and rsi_values[i] < params.rsi_short_threshold  # type: ignore[operator]
            and macd_line[i] < macd_signal[i]  # type: ignore[operator]
        )

    warmup = params.ema_slow + params.macd_signal + 2
    tracker = PositionTracker()
    candidates: list[ReachCandidate] = []
    emissions: dict[int, set[str]] = {}  # index -> actions réellement émises

    def state_at(i: int) -> FormingState | None:
        j = i - 1
        if (
            ema_fast[j] is None
            or ema_slow[j] is None
            or macd_signal[j] is None
            or ema_macd_fast[j] is None
            or ema_macd_slow[j] is None
            or avg_gain[j] is None
            or avg_loss[j] is None
        ):
            return None
        return FormingState(
            prev_close=closes[j],
            ema_fast_prev=ema_fast[j],  # type: ignore[arg-type]
            ema_slow_prev=ema_slow[j],  # type: ignore[arg-type]
            ema_macd_fast_prev=ema_macd_fast[j],  # type: ignore[arg-type]
            ema_macd_slow_prev=ema_macd_slow[j],  # type: ignore[arg-type]
            macd_line_prev=macd_line[j],  # type: ignore[arg-type]
            macd_signal_prev=macd_signal[j],  # type: ignore[arg-type]
            avg_gain_prev=avg_gain[j],  # type: ignore[arg-type]
            avg_loss_prev=avg_loss[j],  # type: ignore[arg-type]
        )

    for i in range(warmup + 1, len(candles)):
        candle = candles[i]
        # ---- auto-validation forme fermée == moteur (à chaque bougie) ----
        st = state_at(i)
        if st is not None:
            assert bullish_at(st, closes[i], params) == engine_bullish(i), (
                f"forme fermée BUY ≠ moteur à i={i}"
            )
            assert bearish_at(st, closes[i], params) == engine_bearish(i), (
                f"forme fermée SELL ≠ moteur à i={i}"
            )

        atr_closed = atr_values[i - 1]  # ATR des bougies fermées uniquement
        if st is not None and atr_closed is not None and atr_closed > 0:
            for direction, cond in (("BUY", bullish_at), ("SELL", bearish_at)):
                # Transition impossible : direction déjà entièrement vraie.
                if direction == "BUY" and engine_bullish(i - 1):
                    continue
                if direction == "SELL" and engine_bearish(i - 1):
                    continue
                level = trigger_level(st, params, direction)
                if level is None:
                    continue
                if score_at(st, level, params) < min_score:
                    continue  # annonce filtrée : le niveau ne passerait pas
                d = abs(level - candle.open) / atr_closed
                candidates.append(
                    ReachCandidate(
                        symbol=symbol,
                        index=i,
                        open_time=candle.open_time,
                        action=direction,
                        level=level,
                        d_atr=d,
                        bucket=_bucket_for(d),
                        fillable_pre=tracker.would_fill(direction),
                    )
                )

        # ---- avancement fidèle du moteur (position simulée) ----
        tracker.apply_candle(candle)
        result = evaluate_at(candles, series, i, params)
        if result is not None and st is not None:
            assert total_score(result) == score_at(st, closes[i], params), (
                f"score forme fermée ≠ moteur à i={i}"
            )
        if (
            result is not None
            and total_score(result) >= min_score
            and tracker.would_fill(result.action)
        ):
            tracker.open(
                result.action, result.entry, result.stop_loss, result.take_profit
            )
            emissions.setdefault(i, set()).add(result.action)

    for cand in candidates:
        for n in horizons:
            _resolve_outcome(cand, candles, emissions, n, params)
    return candidates


def _resolve_outcome(
    cand: ReachCandidate,
    candles: list[Candle],
    emissions: dict[int, set[str]],
    horizon: int,
    params: MomentumParams,
) -> None:
    """Issue du candidat sur ``horizon`` bougies (sémantique spec §3)."""
    opposite = "SELL" if cand.action == "BUY" else "BUY"
    last = min(cand.index + horizon - 1, len(candles) - 1)
    for j in range(cand.index, last + 1):
        c = candles[j]
        touched = c.high >= cand.level if cand.action == "BUY" else c.low <= cand.level
        emits = emissions.get(j, set())
        if touched:
            close = c.close
            if cand.action == "BUY":
                adv = (close - cand.level) / cand.level
            else:
                adv = (cand.level - close) / cand.level
            if cand.action in emits:
                cand.outcomes[horizon] = CONFIRMED
                cand.fill_advantage[horizon] = adv
            else:
                # Annulation à la clôture de la bougie touchante (spec §3).
                cand.outcomes[horizon] = CANCELLED
                cand.bail_out[horizon] = adv
            cand.resolve_idx[horizon] = j
            return
        if cand.action in emits:
            # Signal officiel sans touche : la pré-alerte est consommée en
            # silence mais l'utilisateur limite n'a pas été remplie.
            cand.outcomes[horizon] = SIGNAL_NO_FILL
            cand.resolve_idx[horizon] = j
            return
        if opposite in emits:
            cand.outcomes[horizon] = INVALIDATED
            cand.resolve_idx[horizon] = j
            return
    cand.outcomes[horizon] = EXPIRED
    cand.resolve_idx[horizon] = max(cand.index, last)


# ---------------------------------------------------------------- rapport --


def _pctl(values: list[float], q: float) -> str:
    if not values:
        return "n/a"
    ordered = sorted(values)
    return f"{100 * ordered[int(q * (len(ordered) - 1))]:+.2f} %"


def announce_volume(
    cands: list[ReachCandidate], bucket: float, horizon: int, days: float
) -> float:
    """Annonces/jour avec la règle anti-spam runtime (1 active par direction).

    La qualité du niveau (taux de toucher, avantage) se mesure sur TOUS les
    candidats ; le volume, lui, doit répliquer l'anti-spam de production,
    sinon les annonces consécutives d'un même niveau seraient comptées N fois.

    ``bucket`` est un SEUIL CUMULATIF (k) : tout candidat avec d <= k est
    annonçable — même règle que les critères go/no-go scellés (spec §6).
    """
    active_until: dict[str, int] = {}
    count = 0
    for cand in sorted(cands, key=lambda c: c.index):
        if cand.d_atr > bucket or not cand.fillable_pre:
            continue
        if active_until.get(cand.action, -1) >= cand.index:
            continue  # une annonce de cette direction est encore active
        active_until[cand.action] = cand.resolve_idx.get(horizon, cand.index)
        count += 1
    return count / days if days > 0 else 0.0


def report(
    symbol: str, cands: list[ReachCandidate], days: float, stage: str
) -> None:
    print(f"\n=== {symbol} — {stage.upper()} ({days:.0f} j) ===")
    if not cands:
        print("  aucun candidat")
        return
    for horizon in HORIZONS:
        print(f"\n  -- horizon {horizon} bougie(s) --")
        print(
            "  bucket     n    touché  confirm/touché  no_fill  inval  "
            "fill p25/p50/p75          bail médian   vol/jour"
        )
        for bucket in BUCKETS:
            # seuil CUMULATIF : d <= k (règle d'annonce, critères spec §6)
            subset = [
                c
                for c in cands
                if c.d_atr <= bucket and c.fillable_pre
            ]
            if not subset:
                print(f"  <= {bucket:>4}  {0:>5}    -")
                continue
            outcomes = [c.outcomes[horizon] for c in subset]
            touched = [o for o in outcomes if o in _TOUCHED_OUTCOMES]
            confirmed = [o for o in outcomes if o == CONFIRMED]
            no_fill = [o for o in outcomes if o == SIGNAL_NO_FILL]
            invalid = [o for o in outcomes if o == INVALIDATED]
            advantages = [
                c.fill_advantage[horizon]
                for c in subset
                if c.outcomes[horizon] == CONFIRMED
                and c.fill_advantage.get(horizon) is not None
            ]
            bails = [
                c.bail_out[horizon]
                for c in subset
                if c.outcomes[horizon] == CANCELLED
                and c.bail_out.get(horizon) is not None
            ]
            vol = announce_volume(cands, bucket, horizon, days)
            n = len(subset)
            touch_rate = 100 * len(touched) / n
            conf_rate = (
                100 * len(confirmed) / len(touched) if touched else float("nan")
            )
            print(
                f"  <= {bucket:>4}  {n:>5}  {touch_rate:>5.1f} %  "
                f"{conf_rate:>12.1f} %  {100 * len(no_fill) / n:>6.1f} %  "
                f"{100 * len(invalid) / n:>5.1f} %  "
                f"{_pctl(advantages, 0.25)}/{_pctl(advantages, 0.5)}/"
                f"{_pctl(advantages, 0.75):>8}  "
                f"{_pctl(bails, 0.5):>12}  {vol:>6.2f}"
            )


def split_stage(candles: list[Candle], stage: str) -> tuple[list[Candle], float]:
    """Fenêtre scellée ANTICIPATION.md §5 : IS = jours 1->1095 (les plus
    anciens), OOS = année finale. Retourne (candidats filtrés, jours utiles)."""
    if not candles:
        raise ValueError("historique vide")
    split_ts = candles[0].open_time + IS_DAYS * DAY_MS
    if stage == "is":
        part = [c for c in candles if c.open_time < split_ts]
    else:
        part = [c for c in candles if c.open_time >= split_ts]
    if not part:
        raise ValueError(f"fenêtre {stage} vide : historique trop court")
    days = (part[-1].close_time - part[0].open_time) / DAY_MS
    return part, days


# -------------------------------------------------------------------- CLI --


async def _main_async(args: argparse.Namespace) -> None:
    params = MomentumParams()
    async with httpx.AsyncClient() as client:
        candles = await load_history(
            client, args.symbol, args.timeframe, args.days
        )
    if not candles:
        raise SystemExit(f"Aucune donnée pour {args.symbol}")
    print(
        f"{args.symbol} {args.timeframe}m : {len(candles)} bougies, "
        f"{(candles[-1].close_time - candles[0].open_time) / DAY_MS:.0f} jours "
        f"(min_score={args.min_score}, stage={args.stage})"
    )
    if args.stage == "oos":
        print(
            "ATTENTION : OOS scellée (ANTICIPATION.md §5) — à ne consommer "
            "QU'UNE fois, après figage de (k, N) sur l'IS."
        )
    window, days = split_stage(candles, args.stage)
    candidates = run_reach_study(
        args.symbol, window, params, args.min_score
    )
    report(args.symbol, candidates, days, args.stage)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="15")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--min-score", type=int, default=45)
    parser.add_argument("--stage", choices=("is", "oos"), default="is")
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
