"""Étude de confirmation des touches (niveau d'entrée à l'avance).

Question posée : si le moteur annonçait à l'avance le niveau de prix P* au
delà duquel la bougie en formation CONFIRMERAIT un signal momentum_v1
(l'utilisateur place un ordre limite à P*), quelle fraction de ces touches
se confirmerait réellement à la clôture ?

Mathématique (formes fermées, état pris à la bougie FERMÉE précédente) :
les trois conditions de momentum_v1, évaluées sur la bougie en formation
avec un prix provisoire P, sont monotones croissantes en P (BUY) :
- EMA_i = e_prev + alpha·(P - e_prev)  (récursion exacte, linéaire en P) :
  EMA50 > EMA200 ⟺ e50 + a50(P - e50) > e200 + a200(P - e200)
- MACD ligne(P) = e12 + a12(P - e12) - e26 - a26(P - e26) ; et comme
  signal_i = a9·ligne_i + (1 - a9)·signal_prev, la condition
  « ligne > signal » se réduit à ligne_i(P) > signal_prev
- RSI de Wilder : avg_gain/avg_loss mis à jour avec max(±(P - c_prev), 0),
  monotone en P

Il existe donc UN niveau unique P* par direction et par bougie : touché ne
signifie pas confirmé — c'est précisément ce que l'étude mesure.

Limites assumées :
- l'historique OHLC ne dit pas QUAND le niveau est touché dans la bougie
  (le préavis réel n'est pas mesurable, seule la faisabilité l'est) ;
- le proxy « annoncé » = le plus haut (BUY) / plus bas (SELL) de la bougie
  est entré à ``eps`` du niveau — borne supérieure honnête ;
- la simulation de position réplique PositionTracker (pyramiding=0, SL
  prioritaire) mais l'état au moment de l'annonce est celui d'avant bougie.

Usage :
    python -m engine.touch_study --symbol BTCUSDT --days 1490
    python -m engine.touch_study --symbol ETHUSDT --days 1490 --eps 0.0010
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from dataclasses import dataclass

import httpx

from engine.indicators import ema
from engine.levels import (  # formes fermées : source unique (engine/levels.py)
    FormingState,
    bearish_at,
    bullish_at,
    score_at,
    trigger_level,
    wilder_state as _wilder_state,
)
from engine.position import PositionTracker
from engine.strategy import (
    Candle,
    MomentumParams,
    compute_series,
    evaluate_at,
    total_score,
)
from engine.validation import load_history

DAY_MS = 86_400_000
DEFAULT_DAYS = 1490


# Les formes fermées (FormingState, bullish_at/bearish_at, score_parts,
# trigger_level, wilder_state) vivent dans engine/levels.py : extraites de
# cette étude pour le runtime (engine/advance.py) sans dépendance aux
# modules d'étude. Elles sont importées plus haut, à l'identique.


# ------------------------------------------------------------------ étude --


@dataclass
class TouchEvent:
    symbol: str
    index: int
    action: str
    level: float | None
    near: bool  # le prix est entré à eps du niveau (proxy haut/bas de bougie)
    touched: bool
    confirmed: bool
    fillable_pre: bool = True  # would_fill au moment de l'annonce
    score_ok: bool = False  # score au niveau P* >= min_score (forme fermée)
    emitted: bool = False  # confirmé + score + pyramiding (signal réel)
    fill_advantage: float | None = None  # (close - level)/level, signé sens
    bail_out: float | None = None  # coût de sortie si touché non confirmé


def run_study(
    symbol: str,
    candles: list[Candle],
    params: MomentumParams | None = None,
    eps: float = 0.0015,
    min_score: int = 45,
) -> list[TouchEvent]:
    """Analyse chaque bougie : niveau P*, annonce, touche, confirmation.

    Auto-validation forte : à chaque bougie, la condition en forme fermée au
    close réel DOIT coïncider avec la condition calculée par les séries du
    moteur (sinon AssertionError — les formes fermées seraient fausses).
    """
    params = params or MomentumParams()
    closes = [c.close for c in candles]
    series = compute_series(closes, params)
    ema_fast, ema_slow = series["ema_fast"], series["ema_slow"]
    macd_line, macd_signal = series["macd"], series["macd_signal"]
    rsi_values = series["rsi"]
    ema_macd_fast = ema(closes, params.macd_fast)
    ema_macd_slow = ema(closes, params.macd_slow)
    avg_gain, avg_loss = _wilder_state(closes, params.rsi_len)

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
    events: list[TouchEvent] = []

    def state_at(i: int) -> FormingState | None:
        j = i - 1  # dernière bougie fermée avant la bougie en formation i
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

    emitted_keys: set[tuple[int, str]] = set()
    for i in range(warmup + 1, len(candles)):
        candle = candles[i]
        # ---- auto-validation forme fermée == moteur (à chaque bougie) ----
        st_check = state_at(i)
        if st_check is not None:
            assert bullish_at(st_check, closes[i], params) == engine_bullish(i), (
                f"forme fermée BUY ≠ moteur à i={i}"
            )
            assert bearish_at(st_check, closes[i], params) == engine_bearish(i), (
                f"forme fermée SELL ≠ moteur à i={i}"
            )

        st = st_check
        if st is not None:
            for direction, cond in (("BUY", bullish_at), ("SELL", bearish_at)):
                if direction == "BUY" and engine_bullish(i - 1):
                    continue  # transition impossible (déjà tout haussier)
                if direction == "SELL" and engine_bearish(i - 1):
                    continue
                # Annonce : le meilleur prix de la bougie entre à eps du niveau
                probe = (
                    candle.high * (1 + eps)
                    if direction == "BUY"
                    else candle.low * (1 - eps)
                )
                near = cond(st, probe, params)
                if not near:
                    continue
                touched = cond(st, candle.high if direction == "BUY" else candle.low, params)
                confirmed = cond(st, closes[i], params)
                # À l'annonce (bougie en formation), l'état est celui d'avant
                # le traitement de la bougie i. Un événement non fillable à
                # l'annonce est quand même enregistré : la bougie peut fermer
                # la position (SL/TP) puis laisser place au signal à la
                # clôture — c'est un vrai signal émis sans annonce utile.
                fillable_pre = tracker.would_fill(direction)
                level = trigger_level(st, params, direction)
                score_ok = (
                    level is not None
                    and score_at(st, level, params) >= min_score
                )
                event = TouchEvent(
                    symbol=symbol,
                    index=i,
                    action=direction,
                    level=level,
                    near=near,
                    touched=touched,
                    confirmed=confirmed,
                    fillable_pre=fillable_pre,
                    score_ok=score_ok,
                )
                if touched and level is not None:
                    if direction == "BUY":
                        adv = (closes[i] - level) / level
                    else:
                        adv = (level - closes[i]) / level
                    if confirmed:
                        event.fill_advantage = adv
                    else:
                        event.bail_out = adv  # négatif = clôture au-delà du niveau
                events.append(event)

        # ---- avancement fidèle du moteur (position simulée) ----
        tracker.apply_candle(candle)
        result = evaluate_at(candles, series, i, params)
        if result is not None and st is not None:
            # Auto-validation du scoring en forme fermée : au close réel, le
            # score doit être IDENTIQUE à celui du moteur.
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
            emitted_keys.add((i, result.action))

    for event in events:
        if event.confirmed and (event.index, event.action) in emitted_keys:
            event.emitted = True
    # Cohérence : tout signal réellement émis doit être un événement confirmé.
    for key in emitted_keys:
        assert any(
            (e.index, e.action) == key and e.confirmed for e in events
        ), f"signal émis sans événement confirmé : {key}"
    return events


# ---------------------------------------------------------------- rapport --


def _pct(x: float) -> str:
    return f"{100 * x:+.3f} %"


def _med(values: list[float]) -> str:
    return _pct(statistics.median(values)) if values else "n/a"


def _p90(values: list[float]) -> str:
    if not values:
        return "n/a"
    ordered = sorted(values)
    return _pct(ordered[int(0.9 * (len(ordered) - 1))])


def report(symbol: str, events: list[TouchEvent], eps: float) -> None:
    print(f"\n=== {symbol} — eps d'annonce {100 * eps:.2f} % ===")
    for action in ("BUY", "SELL"):
        subset = [e for e in events if e.action == action]
        if not subset:
            print(f"  {action}: aucun événement")
            continue
        annonces = [e for e in subset if e.near and e.fillable_pre]
        touchees = [e for e in subset if e.touched]
        confirmees = [e for e in subset if e.confirmed]
        emises = [e for e in subset if e.emitted]
        emises_annoncées = [e for e in emises if e.fillable_pre]
        non_conf = [e for e in touchees if not e.confirmed]
        avantages = [e.fill_advantage for e in emises if e.fill_advantage is not None]
        couts = [e.bail_out for e in non_conf if e.bail_out is not None]
        print(f"  {action}:")
        print(f"    annonces (eps)     : {len(annonces)}")
        print(f"    touches            : {len(touchees)}")
        print(f"    confirmées         : {len(confirmees)}")
        print(f"    signaux réels      : {len(emises)}")
        if len(emises) != len(emises_annoncées):
            print(
                f"    dont sans annonce (position fermée intra-bougie) : "
                f"{len(emises) - len(emises_annoncées)}"
            )
        if touchees:
            taux = 100 * len(confirmees) / len(touchees)
            print(f"    confirmation / touche : {taux:.1f} %")
        if annonces:
            taux = 100 * len(emises_annoncées) / len(annonces)
            print(f"    signal réel / annonce : {taux:.1f} %")
        filtrees = [e for e in annonces if e.score_ok]
        emises_f = [e for e in emises_annoncées if e.score_ok]
        if filtrees:
            print(f"    annonces filtrées (score au niveau) : {len(filtrees)}")
            taux = 100 * len(emises_f) / len(filtrees)
            print(f"    signal réel / annonce filtrée : {taux:.1f} %")
            touchees_f = [e for e in filtrees if e.touched]
            confirmees_f = [e for e in filtrees if e.confirmed]
            if touchees_f:
                taux = 100 * len(confirmees_f) / len(touchees_f)
                print(f"    confirmation / touche filtrée : {taux:.1f} %")
        print(f"    avantage fill (médian / p90) : {_med(avantages)} / {_p90(avantages)}")
        if non_conf:
            faibles = [
                e for e in non_conf if e.bail_out is not None and e.bail_out > -0.002
            ]
            print(
                f"    décharge non confirmée (médian / p90) : {_med(couts)} / {_p90(couts)}"
            )
            print(
                f"    décharges < 0,2 % au-delà du niveau : "
                f"{100 * len(faibles) / len(non_conf):.0f} %"
            )


# -------------------------------------------------------------------- CLI --


async def _main_async(args: argparse.Namespace) -> None:
    params = MomentumParams()
    async with httpx.AsyncClient() as client:
        candles = await load_history(
            client, args.symbol, args.timeframe, args.days
        )
    if not candles:
        raise SystemExit(f"Aucune donnée pour {args.symbol}")
    premiers = candles[0].open_time
    derniers = candles[-1].open_time
    print(
        f"{args.symbol} {args.timeframe}m : {len(candles)} bougies, "
        f"{(derniers - premiers) / DAY_MS:.0f} jours "
        f"(min_score={args.min_score})"
    )
    events = run_study(args.symbol, candles, params, args.eps, args.min_score)
    report(args.symbol, events, args.eps)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="15")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--eps", type=float, default=0.0015)
    parser.add_argument("--min-score", type=int, default=45)
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
