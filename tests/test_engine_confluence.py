"""Tests du moteur de confluence v0 et du simulateur de trades.

Vérifiés : resample HTF (anti-lookahead), gâchettes non compensables,
SL structurel borné ATR, score borné et détaillé, propriété de préfixe,
et l'arithmétique exacte du simulateur (frais en R, SL prioritaire).
"""

from __future__ import annotations

import random

import pytest

from engine.confluence import ConfluenceParams, confluence_signals, resample
from engine.confluence_backtest import SignalEntry, compute_metrics, simulate_trades
from engine.strategy import Candle

TF_MS = 900_000
HOUR_MS = 3_600_000


def _candle(i: int, o: float, h: float, l: float, c: float, volume: float = 10.0) -> Candle:
    return Candle(
        open_time=i * TF_MS,
        close_time=i * TF_MS + TF_MS - 1,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=volume,
    )


# ---------------------------------------------------------------- resample


def test_resample_buckets_complets_uniquement() -> None:
    # 6 bougies 15m : 1 bucket 1H complet + 1 partiel (exclu).
    candles = [_candle(i, 100.0, 101.0, 99.0, 100.0 + i) for i in range(6)]
    hourly = resample(candles, HOUR_MS)
    assert len(hourly) == 1
    bucket = hourly[0]
    assert bucket.open_time == 0
    assert bucket.close_time == 3 * TF_MS + TF_MS - 1
    assert bucket.open == 100.0
    assert bucket.close == 103.0
    assert bucket.high == 101.0
    assert bucket.low == 99.0
    assert bucket.volume == 40.0  # 4 bougies du bucket complet


def test_resample_rejette_bucket_incoherent() -> None:
    candles = [_candle(i, 100.0, 101.0, 99.0, 100.0) for i in range(4)]
    try:
        resample(candles, 7 * 60_000)  # 7 min non multiple de 15 min
    except ValueError:
        pass
    else:
        raise AssertionError("un bucket non multiple doit être rejeté")


# ------------------------------------------------- signal de confluence v0


def _sweep_series() -> list[Candle]:
    """Série plate avec swing low en 2 (97), confirmé en 4, balayé en 10."""
    candles = []
    for i in range(11):
        if i == 2:
            candles.append(_candle(i, 100.0, 100.5, 97.0, 100.0))
        elif i == 10:
            candles.append(_candle(10, 100.0, 101.0, 96.5, 100.0))  # sweep
        else:
            candles.append(_candle(i, 100.0, 100.5, 99.0, 100.0))
    return candles


def _small_params() -> ConfluenceParams:
    return ConfluenceParams(sweep_k=2, sweep_min_age=5, atr_len=2)


def test_signal_sur_sweep_avec_sl_borne() -> None:
    signals = confluence_signals(_sweep_series(), None, _small_params())
    assert len(signals) == 1
    s = signals[0]
    assert s.action == "BUY"
    assert s.trigger == "sweep"
    assert s.entry == 100.0
    # ATR(2) à la bougie de sweep : la bougie 10 a TR = 4.5 (wick 96.5/101),
    # ATR = (1.5 + 4.5) / 2 = 3.0. Ancre = min(low 8..10, niveau balayé 97)
    # - 0.25 x ATR = 96.5 - 0.75 = 95.75 ; risque 4.25 dans [1, 2.5] x ATR.
    # L'ATR converge encore vers 1.5 (amorce RMA) : tolérance flottante.
    assert s.stop_loss == pytest.approx(95.75, abs=5e-3)
    assert s.take_profit == pytest.approx(108.5, abs=5e-3)
    assert s.risk_rr == 2.0
    # Score v0 déterministe : trend15 neutral 10, 1H n/a 10, structure 1
    # element 10, momentum neutral 7, volume rvol None 10.
    assert s.score == 47
    assert len(s.details) == 6


def test_gachette_tendance_opposee_bloque() -> None:
    # Même balayage, mais baisse franche avant : EMA rapide < EMA lente
    # (paramètres réduits) -> gâchette tendance 15m opposée -> aucun signal.
    candles = _sweep_series()
    for i in range(8):
        candles[i] = _candle(i, 110.0 - i, 110.5 - i, 108.5 - i, 109.0 - i)
    params = ConfluenceParams(
        sweep_k=2, sweep_min_age=5, atr_len=2, ema_fast=3, ema_slow=5
    )
    signals = confluence_signals(candles, None, params)
    # Le balayage haussier (sens opposé à la tendance) est bloqué : aucun BUY.
    # Des SELL légitimes (retest aligné avec la tendance) peuvent exister.
    assert all(s.action == "SELL" for s in signals)


def test_propriete_de_prefixe_confluence() -> None:
    rng = random.Random(7)
    price = 100.0
    candles = []
    for i in range(160):
        o = price
        c = price + rng.uniform(-2.0, 2.0)
        price = c
        candles.append(
            _candle(i, o, max(o, c) + rng.uniform(0.1, 1.5), min(o, c) - rng.uniform(0.1, 1.5), c)
        )
    params = ConfluenceParams(swing_k=2, sweep_k=3, atr_len=3)
    full = confluence_signals(candles, None, params)
    assert len(full) > 0  # la série produit des candidats
    for cut in (60, 120, 159):
        assert confluence_signals(candles[: cut + 1], None, params) == [
            s for s in full if s.index <= cut
        ]


# ---------------------------------------------------------------- simulateur


def test_simulate_sortie_sl_avec_frais() -> None:
    candles = [
        _candle(0, 99.0, 100.5, 98.5, 100.0),  # signal : entrée 100
        _candle(1, 100.0, 101.0, 97.5, 98.0),  # low 97.5 <= SL 98
    ]
    entries = [SignalEntry(index=0, action="BUY", entry=100.0, stop_loss=98.0, take_profit=104.0)]
    trades = simulate_trades(candles, entries, fee_rate=0.0012)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_index == 1
    assert trade.raw_r == -1.0  # (98 - 100) / 2
    assert abs(trade.net_r - (-1.0 - 100.0 * 0.0012 / 2.0)) < 1e-12  # frais en R


def test_simulate_meme_sens_ignore_renversement_accepte() -> None:
    candles = [
        _candle(0, 99.0, 100.5, 98.5, 100.0),
        _candle(1, 100.0, 101.0, 99.5, 100.5),
        _candle(2, 100.5, 101.5, 100.0, 101.0),
    ]
    entries = [
        SignalEntry(index=0, action="BUY", entry=100.0, stop_loss=98.0, take_profit=110.0),
        SignalEntry(index=1, action="BUY", entry=100.5, stop_loss=98.5, take_profit=110.0),  # ignoré
        SignalEntry(index=2, action="SELL", entry=101.0, stop_loss=103.0, take_profit=95.0),  # renverse
    ]
    trades = simulate_trades(candles, entries, fee_rate=0.0)
    assert len(trades) == 2
    # Achat fermé au renversement (prix d'entrée du SELL) : +0.5 R.
    assert trades[0].raw_r == (101.0 - 100.0) / 2.0
    # Position finale clôturée à la dernière clôture.
    assert trades[1].exit_index == 2
    assert trades[1].raw_r == (101.0 - 101.0) / 2.0


def test_metrics_cohrentes() -> None:
    from engine.confluence_backtest import Trade

    trades = [
        Trade(0, 1, "BUY", "sweep", 70, 2.0, 1.9),
        Trade(2, 3, "SELL", "bos", 60, -1.0, -1.1),
        Trade(4, 5, "BUY", "sweep", 85, -1.0, -1.1),
        Trade(6, 7, "BUY", "sweep", 90, 2.0, 1.9),
    ]
    m = compute_metrics(trades)
    assert m["n"] == 4
    assert m["win_rate"] == 50.0
    assert m["expectancy"] == 0.4
    assert m["profit_factor"] == round(3.8 / 2.2, 2)  # arrondi à 2 décimales
    assert m["total_r"] == 1.6  # 1.9 - 1.1 - 1.1 + 1.9
    assert m["max_losing_streak"] == 2
    assert m["max_drawdown"] == 2.2  # crête 1.9, creux 1.9-1.1-1.1 = -0.3
