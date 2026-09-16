"""Niveaux de déclenchement en formes fermées (production).

Extrait de l'étude de confirmation des touches (engine/touch_study.py) pour
le besoin DU RUNTIME : les pré-alertes (engine/advance.py) s'appuient sur ces
primitives sans dépendre d'aucun module d'étude (validation, confluence...).

Mathématique (état pris à la bougie FERMÉE précédente) : les trois conditions
de momentum_v1, évaluées sur la bougie en formation avec un prix provisoire P,
sont monotones croissantes en P (BUY) :
- EMA_i = e_prev + alpha·(P - e_prev)  (récursion exacte, linéaire en P) :
  EMA50 > EMA200 ⟺ e50 + a50(P - e50) > e200 + a200(P - e200)
- MACD ligne(P) = e12 + a12(P - e12) - e26 - a26(P - e26) ; et comme
  signal_i = a9·ligne_i + (1 - a9)·signal_prev, la condition
  « ligne > signal » se réduit à ligne_i(P) > signal_prev
- RSI de Wilder : avg_gain/avg_loss mis à jour avec max(±(P - c_prev), 0),
  monotone en P

Il existe donc UN niveau unique P* par direction et par bougie (dichotomie).
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.strategy import MomentumParams


@dataclass(frozen=True)
class FormingState:
    """État des indicateurs APRÈS la bougie fermée i-1 (entrée : forms)."""

    prev_close: float
    ema_fast_prev: float
    ema_slow_prev: float
    ema_macd_fast_prev: float  # EMA 12 sous-jacente de la ligne MACD
    ema_macd_slow_prev: float  # EMA 26 sous-jacente
    macd_line_prev: float
    macd_signal_prev: float
    avg_gain_prev: float  # RMA de Wilder des gains, amorce incluse
    avg_loss_prev: float


def wilder_state(
    closes: list[float], length: int
) -> tuple[list[float | None], list[float | None]]:
    """avg_gain / avg_loss alignés sur l'index de close (amorce SMA incluse).

    ag[i] inclut la variation jusqu'à closes[i] inclus (comme ta.rsi).
    """
    n = len(closes)
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, n):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    ag: list[float | None] = [None] * n
    al: list[float | None] = [None] * n
    if len(gains) < length:
        return ag, al
    g_prev = sum(gains[:length]) / length
    l_prev = sum(losses[:length]) / length
    # rma des gains à l'index k inclus ; aligné closes : ag[k + 1]
    ag[length] = g_prev
    al[length] = l_prev
    for k in range(length, len(gains)):
        g_prev = (g_prev * (length - 1) + gains[k]) / length
        l_prev = (l_prev * (length - 1) + losses[k]) / length
        ag[k + 1] = g_prev
        al[k + 1] = l_prev
    return ag, al


def _ema_pair(
    st: FormingState, price: float, p: MomentumParams
) -> tuple[float, float]:
    """(EMA50, EMA200) de la bougie en formation si elle clôture à ``price``."""
    a50, a200 = 2 / (p.ema_fast + 1), 2 / (p.ema_slow + 1)
    return (
        st.ema_fast_prev + a50 * (price - st.ema_fast_prev),
        st.ema_slow_prev + a200 * (price - st.ema_slow_prev),
    )


def _macd_line(st: FormingState, price: float, p: MomentumParams) -> float:
    """Ligne MACD de la bougie en formation à ``price`` (linéaire en P)."""
    a12, a26 = 2 / (p.macd_fast + 1), 2 / (p.macd_slow + 1)
    return (
        st.ema_macd_fast_prev
        + a12 * (price - st.ema_macd_fast_prev)
        - st.ema_macd_slow_prev
        - a26 * (price - st.ema_macd_slow_prev)
    )


def _rsi(st: FormingState, price: float, p: MomentumParams) -> float | None:
    """RSI de Wilder de la bougie en formation à ``price`` (None si na)."""
    length = p.rsi_len
    dp = price - st.prev_close
    gain = max(dp, 0.0)
    loss = max(-dp, 0.0)
    ag = (st.avg_gain_prev * (length - 1) + gain) / length
    al = (st.avg_loss_prev * (length - 1) + loss) / length
    if ag + al == 0.0:
        return None  # na en Pine
    return 100.0 * ag / (ag + al)


def bullish_at(st: FormingState, price: float, p: MomentumParams) -> bool:
    """Les conditions BUY sont-elles vraies si la bougie clôture à ``price`` ?"""
    ema50, ema200 = _ema_pair(st, price, p)
    trend = ema50 > ema200
    # signal_i = a9·ligne_i + (1 - a9)·signal_prev ⟺ ligne > signal_prev
    macd = _macd_line(st, price, p) > st.macd_signal_prev
    rsi = _rsi(st, price, p)
    rsi_ok = rsi is not None and rsi > p.rsi_long_threshold
    return trend and macd and rsi_ok


def bearish_at(st: FormingState, price: float, p: MomentumParams) -> bool:
    """Les conditions SELL sont-elles vraies si la bougie clôture à ``price`` ?"""
    ema50, ema200 = _ema_pair(st, price, p)
    trend = ema50 < ema200
    macd = _macd_line(st, price, p) < st.macd_signal_prev
    rsi = _rsi(st, price, p)
    rsi_ok = rsi is not None and rsi < p.rsi_short_threshold
    return trend and macd and rsi_ok


def score_parts(
    st: FormingState, price: float, p: MomentumParams
) -> tuple[int, int, int]:
    """Composantes du score (barème Phase 26) au prix provisoire ``price``.

    Miroir exact du scoring de ``evaluate_at`` (engine/strategy.py), en
    formes fermées : permet de savoir À L'AVANCE si le niveau P* passerait
    le filtre ENGINE_MIN_SCORE. Retourne (tendance, momentum, MACD).
    """
    ema50, ema200 = _ema_pair(st, price, p)
    trend_up, trend_down = ema50 > ema200, ema50 < ema200
    trend_sep = abs(ema50 / ema200 - 1)
    score_trend = 20 if (trend_up or trend_down) and trend_sep > 0.02 else 10
    rsi = _rsi(st, price, p)
    rsi_force = abs(rsi - 50) if rsi is not None else 0.0
    score_momentum = 20 if rsi_force >= 15 else 10
    a9 = 2 / (p.macd_signal + 1)
    line = _macd_line(st, price, p)
    hist_now = line - (a9 * line + (1 - a9) * st.macd_signal_prev)
    hist_prev = st.macd_line_prev - st.macd_signal_prev
    score_macd = 15 if abs(hist_now) > abs(hist_prev) else 8
    return score_trend, score_momentum, score_macd


def score_at(st: FormingState, price: float, p: MomentumParams) -> int:
    """Score total au prix provisoire ``price`` (voir ``score_parts``)."""
    return sum(score_parts(st, price, p))


def trigger_level(
    st: FormingState,
    p: MomentumParams,
    direction: str,
    band: float = 0.20,
) -> float | None:
    """Niveau P* exact (dichotomie, conditions monotones).

    BUY  : conditions vraies ⟺ P ≥ P*   → P* = plus petit prix confirmant.
    SELL : conditions vraies ⟺ P ≤ P*   → P* = plus grand prix confirmant.
    Retourne None si le niveau est hors de ±``band`` (inatteignable en 15m).
    Si les conditions sont déjà vraies sur toute la bande (dérive pure des
    indicateurs, sans niveau de prix utile), retourne la borne basse/haute.
    """
    cond = bullish_at if direction == "BUY" else bearish_at
    base = st.prev_close
    lo, hi = base * (1 - band), base * (1 + band)
    if not cond(st, hi if direction == "BUY" else lo, p):
        return None  # inatteignable dans la bande
    if cond(st, lo if direction == "BUY" else hi, p):
        # déjà vrai partout : pas de niveau franchissable utile
        return lo if direction == "BUY" else hi
    for _ in range(80):
        mid = (lo + hi) / 2
        if cond(st, mid, p) == (direction == "BUY"):
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2
