"""Tests du portage Momentum V1 (engine/strategy.py).

Séries synthétiques déterministes : plateau (amorce), tendance baissière
(transition SELL unique), puis rallye (transition BUY unique). Les
transitions doivent être uniques et les prix cohérents.
"""

import time

from app.config.settings import Settings
from app.signals.schemas import TradingViewSignal
from app.signals.validator import validate_signal
from engine.backtest import replay
from engine.strategy import (
    Candle,
    MomentumParams,
    SignalResult,
    build_payload,
    evaluate_momentum_v1,
)

INTERVAL_MS = 900_000  # 15 minutes


def make_candles(closes: list[float], start_ms: int = 0) -> list[Candle]:
    return [
        Candle(
            open_time=start_ms + i * INTERVAL_MS,
            close_time=start_ms + (i + 1) * INTERVAL_MS - 1,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1.0,
        )
        for i, close in enumerate(closes)
    ]


def plateau_puis_baisse_puis_rallye() -> list[float]:
    closes = [300.0] * 220                     # plateau : amorce (RSI = na)
    closes += [300.0 - 0.5 * i for i in range(1, 201)]   # baisse -> SELL
    closes += [closes[-1] * (1.02**k) for k in range(1, 151)]  # rallye -> BUY
    return closes


def plateau_puis_hausse_puis_baisse() -> list[float]:
    closes = [300.0] * 220
    closes += [300.0 + 0.5 * i for i in range(1, 201)]   # hausse -> BUY
    closes += [closes[-1] * (0.98**k) for k in range(1, 151)]  # baisse -> SELL
    return closes


def test_aucun_signal_sur_serie_plate() -> None:
    candles = make_candles([300.0] * 400)
    assert replay(candles) == ([], 0)
    assert evaluate_momentum_v1(candles, MomentumParams()) is None


def test_historique_insuffisant_retourne_none() -> None:
    candles = make_candles([float(i) for i in range(100)])
    assert evaluate_momentum_v1(candles, MomentumParams()) is None


def test_transition_sell_puis_buy() -> None:
    candles = make_candles(plateau_puis_baisse_puis_rallye())
    signals, ignored = replay(candles)
    assert [s.action for s in signals] == ["SELL", "BUY"]

    # Cohérence interne : l'entrée est la clôture de la bougie de transition.
    by_open = {c.open_time: c for c in candles}
    for signal in signals:
        assert signal.entry == by_open[signal.candle_open_time].close

    buy = signals[1]
    assert buy.stop_loss == buy.entry * (1 - 0.01)
    assert buy.take_profit == buy.entry * (1 + 0.02)
    sell = signals[0]
    assert sell.stop_loss == sell.entry * (1 + 0.01)
    assert sell.take_profit == sell.entry * (1 - 0.02)


def test_transition_buy_puis_sell() -> None:
    candles = make_candles(plateau_puis_hausse_puis_baisse())
    signals, ignored = replay(candles)
    assert [s.action for s in signals] == ["BUY", "SELL"]


def test_scores_dans_les_bornes() -> None:
    candles = make_candles(plateau_puis_baisse_puis_rallye())
    signals, _ = replay(candles)
    for signal in signals:
        assert signal.score_trend in (10, 20)
        assert signal.score_momentum in (10, 20)
        assert signal.score_macd in (8, 15)


def test_timestamp_de_cloture_est_la_borne_de_la_bougie() -> None:
    candles = make_candles(plateau_puis_baisse_puis_rallye())
    signal = replay(candles)[0][0]
    # close_time Binance = open + 900_000 - 1 -> borne = open + 900_000
    assert signal.candle_close_time == signal.candle_open_time + INTERVAL_MS


def test_payload_conforme_au_schema_et_valide() -> None:
    now_ms = int(time.time() * 1000)
    result = SignalResult(
        action="BUY",
        entry=100_000.0,
        stop_loss=99_000.0,
        take_profit=102_000.0,
        score_trend=20,
        score_momentum=20,
        score_macd=15,
        candle_open_time=now_ms - INTERVAL_MS,
        candle_close_time=now_ms,
    )
    payload = build_payload(result, symbol="BTCUSDT", timeframe="15", secret="secret-test")

    # Le payload doit être accepté tel quel par le schéma du backend.
    signal = TradingViewSignal(**payload)
    assert signal.strategy == "momentum_v1"
    assert signal.symbol == "BTCUSDT"
    assert signal.exchange == "BINANCE"
    assert signal.timeframe == "15"
    assert signal.price == 100_000.0

    # Et passer la validation métier (listes blanches, cohérence, fraîcheur).
    settings = Settings(_env_file=None)  # secrets factices de conftest.py
    assert settings.tradingview_webhook_secret == "secret-test"
    result_validation = validate_signal(signal, settings)
    assert result_validation.valid, result_validation.reason


def test_payload_sell_coherent() -> None:
    now_ms = int(time.time() * 1000)
    result = SignalResult(
        action="SELL",
        entry=100_000.0,
        stop_loss=101_000.0,
        take_profit=98_000.0,
        score_trend=10,
        score_momentum=10,
        score_macd=8,
        candle_open_time=now_ms - INTERVAL_MS,
        candle_close_time=now_ms,
    )
    payload = build_payload(result, symbol="ETHUSDT", timeframe="15", secret="secret-test")
    signal = TradingViewSignal(**payload)
    settings = Settings(_env_file=None)
    assert validate_signal(signal, settings).valid
