"""Signaux à l'avance (pré-alertes sur la bougie en formation).

Fondement (étude de confirmation des touches, engine/touch_study.py, 4 ans
de données 15m) : les trois conditions de momentum_v1, évaluées sur la
bougie en formation avec un prix provisoire P, sont monotones en P. Il
existe donc UN niveau exact P* par direction et par bougie — celui qui
confirmerait le signal à la clôture si la bougie finissait au-delà.

Ce module détecte, PENDANT la formation de la bougie, le moment où le prix
s'approche (à ``eps``) d'un tel niveau P* qui passerait le filtre qualité :
l'utilisateur peut placer un ordre limite à P* et être rempli AVANT la
clôture, au lieu de découvrir le signal après (étude : avantage de fill
médian +0,4 à +0,5 %). Touché ne signifie pas confirmé (~50 % de
confirmation) : si le niveau est touché mais que la bougie ne confirme pas,
une annulation est envoyée (décharge médiane -0,1 %, 70-84 % < 0,2 %).

Garanties :
- AUCUN impact sur le pipeline officiel : pas d'ordre, pas de position
  simulée, pas de signal_uid, pas de numéro de trade — la pré-alerte est
  purement informative (best-effort) ;
- l'état des indicateurs est pris sur les bougies FERMÉES uniquement
  (anti-repainting) ;
- une seule annonce par bougie en formation ;
- mêmes barèmes SL/TP/score que le moteur (params.sl_pct/tp_pct, Phase 26).
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.indicators import atr, ema
from engine.strategy import Candle, MomentumParams, compute_series
from engine.touch_study import (
    FormingState,
    _wilder_state,
    bearish_at,
    bullish_at,
    score_parts,
    trigger_level,
)

ADVANCE_STRATEGY = "momentum_v1"

# Affichage honnête du mode anticipatif (ANTICIPATION.md §7, amendement v1.1) :
# taux de toucher mesuré à (k=0,50 ; N=2) sur 4 ans IS+OOS poolés
# (BTC 74,1 %, ETH 69,5 %). Ce n'est NI une probabilité de gain, NI un
# avantage de prix (fill ≈ neutre : +0,14/+0,18 % médian, coût
# d'annulation symétrique).
ANTICIPATIVE_TOUCH_RATE = 0.70


@dataclass(frozen=True)
class AdvancePlan:
    """Pré-alerte calculée sur la bougie en formation."""

    action: str  # "BUY" | "SELL"
    level: float  # P* : niveau qui confirmerait le signal à la clôture
    stop_loss: float
    take_profit: float
    risk_reward: float
    score_trend: int
    score_momentum: int
    score_macd: int


def forming_state(
    closed: list[Candle], params: MomentumParams
) -> FormingState | None:
    """État des indicateurs après la DERNIÈRE bougie fermée.

    None si l'historique fermé est insuffisant (amortissement EMA200 + MACD)
    ou contient des valeurs na.
    """
    if len(closed) < params.ema_slow + params.macd_signal + 2:
        return None
    closes = [c.close for c in closed]
    series = compute_series(closes, params)
    j = len(closes) - 1
    e12 = ema(closes, params.macd_fast)[j]
    e26 = ema(closes, params.macd_slow)[j]
    avg_gain, avg_loss = _wilder_state(closes, params.rsi_len)
    values = (
        series["ema_fast"][j],
        series["ema_slow"][j],
        e12,
        e26,
        series["macd"][j],
        series["macd_signal"][j],
        avg_gain[j],
        avg_loss[j],
    )
    if any(v is None for v in values):
        return None
    return FormingState(
        prev_close=closes[j],
        ema_fast_prev=series["ema_fast"][j],  # type: ignore[arg-type]
        ema_slow_prev=series["ema_slow"][j],  # type: ignore[arg-type]
        ema_macd_fast_prev=e12,  # type: ignore[arg-type]
        ema_macd_slow_prev=e26,  # type: ignore[arg-type]
        macd_line_prev=series["macd"][j],  # type: ignore[arg-type]
        macd_signal_prev=series["macd_signal"][j],  # type: ignore[arg-type]
        avg_gain_prev=avg_gain[j],  # type: ignore[arg-type]
        avg_loss_prev=avg_loss[j],  # type: ignore[arg-type]
    )


def plan_advance(
    closed: list[Candle],
    forming: Candle,
    params: MomentumParams,
    min_score: int = 0,
    eps: float = 0.0015,
) -> AdvancePlan | None:
    """Y a-t-il un niveau P* à annoncer pour la bougie ``forming`` ?

    Conditions (toutes issues de l'étude, les mêmes que ses « annonces ») :
    - état des indicateurs pris après la dernière bougie FERMÉE ;
    - la direction n'est pas déjà vraie à la clôture précédente (une
      transition reste possible) ;
    - le meilleur prix atteint par la bougie en formation est entré à
      ``eps`` du niveau (proxy haut/bas, comme dans l'étude) ;
    - le niveau existe (trigger_level, bande ±20 %) ;
    - le score évalué AU NIVEAU passe le filtre ENGINE_MIN_SCORE (les
      annonces non filtrées sont ~8/jour à 3 % de signaux réels ; filtrées
      ~0,3/jour à ~30 %).
    """
    st = forming_state(closed, params)
    if st is None:
        return None
    for direction, cond, extreme in (
        ("BUY", bullish_at, forming.high),
        ("SELL", bearish_at, forming.low),
    ):
        # Transition impossible : la direction est déjà entièrement vraie
        # à la clôture précédente (même garde-fou que l'étude).
        if cond(st, st.prev_close, params):
            continue
        probe = extreme * (1 + eps) if direction == "BUY" else extreme * (1 - eps)
        if not cond(st, probe, params):
            continue  # pas encore assez proche du niveau
        level = trigger_level(st, params, direction)
        if level is None:
            continue
        score_trend, score_momentum, score_macd = score_parts(st, level, params)
        if score_trend + score_momentum + score_macd < min_score:
            continue
        if direction == "BUY":
            stop_loss = level * (1 - params.sl_pct)
            take_profit = level * (1 + params.tp_pct)
        else:
            stop_loss = level * (1 + params.sl_pct)
            take_profit = level * (1 - params.tp_pct)
        return AdvancePlan(
            action=direction,
            level=level,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=params.tp_pct / params.sl_pct,
            score_trend=score_trend,
            score_momentum=score_momentum,
            score_macd=score_macd,
        )
    return None


def build_advance_payload(
    plan: AdvancePlan, *, symbol: str, timeframe: str, secret: str
) -> dict:
    """JSON de la pré-alerte vers /internal/prealert du backend."""
    return {
        "kind": "advance",
        "secret": secret,
        "strategy": ADVANCE_STRATEGY,
        "symbol": symbol,
        "timeframe": timeframe,
        "action": plan.action,
        "price": plan.level,
        "stop_loss": plan.stop_loss,
        "take_profit": plan.take_profit,
        "risk_reward": plan.risk_reward,
        "score_trend": plan.score_trend,
        "score_momentum": plan.score_momentum,
        "score_macd": plan.score_macd,
    }


def build_invalidation_payload(
    *, symbol: str, timeframe: str, secret: str, action: str, level: float
) -> dict:
    """JSON de l'annulation (niveau touché, bougie non confirmée à la clôture)."""
    return {
        "kind": "invalidated",
        "secret": secret,
        "strategy": ADVANCE_STRATEGY,
        "symbol": symbol,
        "timeframe": timeframe,
        "action": action,
        "price": level,
    }


# --------------------------------------------- mode anticipatif (ANTICIPATION.md) --


def atr_closed(closed: list[Candle], length: int = 14) -> float | None:
    """ATR des bougies FERMÉES (dernière valeur). Anti-lookahead : la bougie
    en formation n'entre jamais dans ce calcul (spec §4)."""
    if len(closed) < length + 1:
        return None
    value = atr(
        [c.high for c in closed],
        [c.low for c in closed],
        [c.close for c in closed],
        length,
    )[-1]
    return value


def plan_anticipative(
    closed: list[Candle],
    forming: Candle,
    params: MomentumParams,
    min_score: int = 0,
    k_atr: float = 0.50,
) -> AdvancePlan | None:
    """Y a-t-il un niveau P* à annoncer DÈS L'OUVERTURE de ``forming`` ?

    Différence avec ``plan_advance`` (mode réactif) : aucune condition de
    proximité — le niveau est annoncé à l'ouverture s'il est ATTEIGNABLE,
    c'est-à-dire si la distance |P* − open| <= ``k_atr`` × ATR14 des bougies
    fermées (spec ANTICIPATION.md §2). Seul l'OPEN de la bougie en formation
    est lu (jamais son high/low, spec §4).

    OOS validée (2026-09-14) : à (k=0,50 ; N=2) le niveau est touché dans
    ~70 % des cas (BTC 74,1 %, ETH 69,5 %, IS+OOS poolés).
    """
    st = forming_state(closed, params)
    if st is None:
        return None
    atr_value = atr_closed(closed)
    if atr_value is None or atr_value <= 0:
        return None
    for direction, cond in (("BUY", bullish_at), ("SELL", bearish_at)):
        if cond(st, st.prev_close, params):
            continue  # transition impossible (déjà entièrement vrai)
        level = trigger_level(st, params, direction)
        if level is None:
            continue
        score_trend, score_momentum, score_macd = score_parts(st, level, params)
        if score_trend + score_momentum + score_macd < min_score:
            continue
        if abs(level - forming.open) > k_atr * atr_value:
            continue  # inatteignable au seuil k : pas d'annonce
        if direction == "BUY":
            stop_loss = level * (1 - params.sl_pct)
            take_profit = level * (1 + params.tp_pct)
        else:
            stop_loss = level * (1 + params.sl_pct)
            take_profit = level * (1 - params.tp_pct)
        return AdvancePlan(
            action=direction,
            level=level,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=params.tp_pct / params.sl_pct,
            score_trend=score_trend,
            score_momentum=score_momentum,
            score_macd=score_macd,
        )
    return None


def build_anticipative_payload(
    plan: AdvancePlan,
    *,
    symbol: str,
    timeframe: str,
    secret: str,
    horizon: int,
) -> dict:
    """JSON de la pré-alerte anticipative (annonce à l'ouverture)."""
    payload = build_advance_payload(
        plan, symbol=symbol, timeframe=timeframe, secret=secret
    )
    payload["kind"] = "advance"
    payload["expires_in"] = horizon
    payload["touch_rate"] = ANTICIPATIVE_TOUCH_RATE
    return payload


def build_confirmed_payload(
    *, symbol: str, timeframe: str, secret: str, action: str, level: float
) -> dict:
    """JSON du message « signal validé » (touché ET confirmé à la clôture,
    amendement v1.1 — envoyé en plus du signal officiel)."""
    return {
        "kind": "confirmed",
        "secret": secret,
        "strategy": ADVANCE_STRATEGY,
        "symbol": symbol,
        "timeframe": timeframe,
        "action": action,
        "price": level,
    }


def build_expiration_payload(
    *, symbol: str, timeframe: str, secret: str, action: str, level: float
) -> dict:
    """JSON de l'expiration (niveau jamais touché à l'horizon : retirer
    l'ordre limite)."""
    return {
        "kind": "expired",
        "secret": secret,
        "strategy": ADVANCE_STRATEGY,
        "symbol": symbol,
        "timeframe": timeframe,
        "action": action,
        "price": level,
    }
