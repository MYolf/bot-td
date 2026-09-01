"""Tests des features de qualification (tendance, momentum, volatilité,
volume, VWAP) et de l'outil d'étude (forward returns).

Garanties :
1. ces features ne portent JAMAIS de logique de déclenchement (rôle :
   qualification uniquement — CONFLUENCE.md) ;
2. valeurs attendues sur séries construites à la main ;
3. propriété de PRÉFIXE (anti-lookahead) pour toutes les features.
"""

from __future__ import annotations

import random

from engine.features import (
    BULLISH,
    NEUTRAL,
    momentum_states,
    trend_states,
    volume_states,
    volatility_states,
    vwap_daily,
)
from engine.feature_study import forward_returns
from engine.strategy import Candle

TF_MS = 900_000
DAY_MS = 86_400_000


def _mk(
    closes: list[float],
    volumes: list[float] | None = None,
    tf_ms: int = TF_MS,
    start_time: int = 0,
) -> list[Candle]:
    volumes = volumes or [10.0] * len(closes)
    return [
        Candle(
            open_time=start_time + i * tf_ms,
            close_time=start_time + i * tf_ms + tf_ms - 1,
            open=close,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=volumes[i],
        )
        for i, close in enumerate(closes)
    ]


def _candle(open_time: int, price: float, volume: float = 1.0) -> Candle:
    return Candle(
        open_time=open_time,
        close_time=open_time + TF_MS - 1,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=volume,
    )


# ------------------------------------------------------------------ tendance


def test_trend_etats_amorce_puis_biais() -> None:
    closes = [float(v) for v in range(1, 9)]  # hausse régulière
    states = trend_states(_mk(closes), ema_fast_len=2, ema_slow_len=3)
    # Amorce : EMA lente non définie -> neutral, champs absents.
    assert states[0].bias == NEUTRAL
    assert states[0].separation is None
    # i=2 : ema2=2.5 > ema3=2.0 (amorce SMA : la rapide part plus vite).
    assert states[2].bias == BULLISH
    assert states[3].bias == BULLISH
    assert states[3].price_above_fast is True
    assert states[3].separation is not None and states[3].separation > 0.0
    # Pente définie dès que l'historique le permet, positive en hausse.
    assert states[3].slope is None  # i < slope_bars (5)
    assert states[7].slope is not None and states[7].slope > 0.0


# ------------------------------------------------------------------ momentum


def test_momentum_fusion_rsi_macd() -> None:
    # Hausse ACCÉLÉRANTE : la ligne MACD s'écarte de son signal (hist > 0),
    # contrairement à une hausse linéaire où elle converge exactement.
    closes = [float(v * v) for v in range(1, 13)]
    states = momentum_states(_mk(closes), rsi_len=2, macd_fast=3, macd_slow=5, macd_signal=2)
    # Amorce : rien de défini -> neutral.
    assert states[0].bias == NEUTRAL
    assert states[0].rsi is None
    assert states[0].macd_hist is None
    assert states[0].hist_expanding is None
    # En hausse franche : RSI ~100 ET MACD ligne > signal -> bullish.
    assert states[-1].bias == BULLISH
    assert states[-1].rsi is not None and states[-1].rsi > 50.0
    assert states[-1].hist_expanding is not None


def test_momentum_neutral_si_les_deux_familles_divergent() -> None:
    # Série en dents de scie : RSI et MACD rarement alignés -> majorité neutral.
    rng = random.Random(7)
    closes = [100.0]
    for _ in range(60):
        closes.append(closes[-1] + rng.uniform(-3.0, 3.0))
    states = momentum_states(_mk(closes), rsi_len=3, macd_fast=3, macd_slow=5, macd_signal=2)
    counts = {b: sum(1 for s in states if s.bias == b) for b in {s.bias for s in states}}
    # La fusion exige l'accord des deux familles : neutral domine sur du bruit.
    assert max(counts, key=counts.get) == NEUTRAL


# ---------------------------------------------------------------- volatilité


def test_volatilite_atr_et_expansion() -> None:
    candles = _mk([100.0] * 12)  # TR constant = 2 (high-low = 2)
    # Choc de volatilité sur la bougie 9 : range de 10.
    candles[9] = Candle(
        open_time=9 * TF_MS,
        close_time=9 * TF_MS + TF_MS - 1,
        open=100.0,
        high=105.0,
        low=95.0,
        close=100.0,
        volume=10.0,
    )
    states = volatility_states(candles, atr_len=2, ref_bars=3)
    assert states[0].atr is None  # premier TR non défini
    assert states[8].atr == 2.0  # régime constant
    assert states[8].atr_pct is not None and abs(states[8].atr_pct - 0.02) < 1e-9
    # Après le choc : ATR monte (rma), expansion > 0.
    assert states[9].atr == 6.0  # rma(2) : (2 * 1 + 10) / 2
    assert states[9].expansion is not None and states[9].expansion > 0.0


# -------------------------------------------------------------------- volume


def test_volume_rvol() -> None:
    volumes = [10.0, 10.0, 10.0, 10.0, 30.0]
    states = volume_states(_mk([100.0] * 5, volumes), avg_len=3)
    assert states[2].rvol is None  # SMA des précédentes non amorcée
    assert states[3].rvol == 1.0  # 10 / (10+10+10)/3
    assert states[4].rvol == 3.0  # 30 / (10+10+10)/3 — pas d'auto-dilution


# --------------------------------------------------------------------- vwap


def test_vwap_reset_journalier_utc() -> None:
    candles = [
        _candle(DAY_MS - 2 * TF_MS, 10.0),  # jour J, 23:30
        _candle(DAY_MS - TF_MS, 20.0),  # jour J, 23:45
        _candle(DAY_MS, 30.0),  # jour J+1, 00:00 -> reset
    ]
    vwap = vwap_daily(candles)
    assert vwap[0] == 10.0
    assert vwap[1] == 15.0  # moyenne cumulée du même jour
    assert vwap[2] == 30.0  # nouveau jour : repart de zéro


# --------------------------------------------------------- forward returns


def test_forward_returns_valeurs_connues() -> None:
    closes = [100.0, 110.0, 99.0, 105.0]
    rets = forward_returns(closes, 2)
    assert rets[0] == 99.0 / 100.0 - 1.0
    assert rets[1] == 105.0 / 110.0 - 1.0
    assert rets[2] is None
    assert rets[3] is None


# ------------------------------------------------- propriété de préfixe


def test_propriete_de_prefixe_toutes_features() -> None:
    rng = random.Random(42)
    candles = [
        Candle(
            open_time=i * TF_MS,
            close_time=i * TF_MS + TF_MS - 1,
            open=100.0,
            high=100.0 + rng.uniform(0.5, 3.0),
            low=100.0 - rng.uniform(0.5, 3.0),
            close=100.0 + rng.uniform(-2.0, 2.0),
            volume=rng.uniform(5.0, 50.0),
        )
        for i in range(140)
    ]
    ref_trend = trend_states(candles, ema_fast_len=5, ema_slow_len=20)
    ref_momentum = momentum_states(candles, rsi_len=5, macd_fast=3, macd_slow=8, macd_signal=3)
    ref_vol = volatility_states(candles, atr_len=5, ref_bars=10)
    ref_volume = volume_states(candles, avg_len=10)
    ref_vwap = vwap_daily(candles)
    for cut in (40, 90, 139):
        prefix = candles[: cut + 1]
        assert trend_states(prefix, ema_fast_len=5, ema_slow_len=20) == ref_trend[: cut + 1]
        assert (
            momentum_states(prefix, rsi_len=5, macd_fast=3, macd_slow=8, macd_signal=3)
            == ref_momentum[: cut + 1]
        )
        assert volatility_states(prefix, atr_len=5, ref_bars=10) == ref_vol[: cut + 1]
        assert volume_states(prefix, avg_len=10) == ref_volume[: cut + 1]
        assert vwap_daily(prefix) == ref_vwap[: cut + 1]
