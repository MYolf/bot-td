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


# ---------------------------------------------- parité evaluate_at (étude) --


def test_evaluate_at_identique_au_prefixe() -> None:
    """compute_series + evaluate_at == evaluate_momentum_v1 par préfixe.

    Garantit que l'évaluation rapide multi-bougies (études/backtests O(n))
    donne EXACTEMENT les mêmes transitions que la fonction de production.
    """
    import random

    from engine.strategy import MomentumParams, compute_series, evaluate_at

    rng = random.Random(42)
    params = MomentumParams(ema_slow=30, macd_fast=5, macd_slow=10)  # amorce courte, MACD valide
    min_len = params.ema_slow + params.macd_signal + 2
    # Marché synthétique : marche aléatoire avec dérive par segments.
    candles = []
    price = 100.0
    t = 0
    for _ in range(min_len + 260):
        drift = 0.02 if (t // 900_000 // 60) % 2 == 0 else -0.02
        o = price
        c = o + drift + rng.uniform(-0.4, 0.4)
        h = max(o, c) + rng.uniform(0.0, 0.3)
        l = min(o, c) - rng.uniform(0.0, 0.3)
        candles.append(
            Candle(t, t + 900_000 - 1, round(o, 2), round(h, 2), round(l, 2), round(c, 2), 10.0)
        )
        price = c
        t += 900_000

    closes = [c.close for c in candles]
    series = compute_series(closes, params)
    transitions = 0
    for i in range(min_len - 1, len(candles)):
        attendu = evaluate_momentum_v1(candles[: i + 1], params)
        rapide = evaluate_at(candles, series, i, params)
        if attendu is None:
            assert rapide is None
        else:
            assert rapide is not None
            assert rapide == attendu  # dataclass frozen : égalité champ à champ
            transitions += 1
    # Le marché synthétique doit produire des transitions (test non vide).
    assert transitions >= 5


# ------------------------------------------ momentum_study (audit rapide) --


def test_all_transitions_identiques_au_replay_lent() -> None:
    """Le passage unique O(n) retrouve les transitions du replay O(n²)."""
    import random

    from engine.backtest import replay
    from engine.momentum_study import all_transitions

    rng = random.Random(7)
    params = MomentumParams(ema_slow=20, macd_fast=4, macd_slow=10)
    candles = []
    price = 100.0
    t = 0
    for _ in range(600):
        drift = 0.03 if (t // 900_000 // 40) % 2 == 0 else -0.03
        o = price
        c = o + drift + rng.uniform(-0.4, 0.4)
        candles.append(
            Candle(
                t,
                t + 900_000 - 1,
                o,
                max(o, c) + 0.1,
                min(o, c) - 0.1,
                c,
                10.0,
            )
        )
        price = c
        t += 900_000

    rapides = all_transitions(candles, params)
    # replay() filtre par position (pyramiding 0) ; les transitions brutes
    # contiennent donc un sur-ensemble : chaque signal émis par replay doit
    # être présent, aux mêmes caracteristiques près.
    emis, _ignores = replay(candles, params)
    rapide_par_temps = {r.candle_open_time: r for r in rapides}
    for signal in emis:
        assert signal.candle_open_time in rapide_par_temps
        correspondant = rapide_par_temps[signal.candle_open_time]
        assert correspondant.action == signal.action
        assert correspondant.entry == signal.entry
        assert correspondant.stop_loss == signal.stop_loss
        assert correspondant.take_profit == signal.take_profit


def test_to_entries_filtres_score_et_sens() -> None:
    from engine.momentum_study import to_entries, total_score

    class FauxSignal:
        def __init__(self, action, open_time, scores):
            self.action = action
            self.candle_open_time = open_time
            self.entry = 100.0
            self.stop_loss = 99.0
            self.take_profit = 102.0
            self.score_trend, self.score_momentum, self.score_macd = scores

    candles = [Candle(i * 900_000, i * 900_000 + 899_999, 1, 1, 1, 1, 1) for i in range(3)]
    signaux = [
        FauxSignal("BUY", 0, (20, 20, 15)),   # total 55
        FauxSignal("SELL", 900_000, (10, 10, 8)),  # total 28
        FauxSignal("BUY", 1_800_000, (20, 10, 8)),  # total 38
    ]
    assert total_score(signaux[0]) == 55
    # Sans filtre : les trois passent, index résolus par open_time.
    toutes = to_entries(candles, signaux)
    assert [e.index for e in toutes] == [0, 1, 2]
    assert toutes[0].score == 55
    # Filtre score >= 38 : le SELL à 28 sort.
    assert [e.score for e in to_entries(candles, signaux, min_score=38)] == [55, 38]
    # Filtre BUY uniquement.
    assert [e.action for e in to_entries(candles, signaux, side="buy")] == ["BUY", "BUY"]
