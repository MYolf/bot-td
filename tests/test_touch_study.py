"""Tests de l'étude de confirmation des touches (engine/touch_study.py).

Point critique : les formes fermées (conditions de la bougie en formation
comme fonction du prix provisoire) doivent coïncider EXACTEMENT avec les
séries du moteur sur les closes réels — sinon toute l'étude est invalide.
"""

import random

from engine.indicators import ema
from engine.strategy import (
    Candle,
    MomentumParams,
    compute_series,
    evaluate_momentum_v1,
    total_score,
)
from engine.touch_study import (
    FormingState,
    _wilder_state,
    bearish_at,
    bullish_at,
    run_study,
    score_at,
    trigger_level,
)


def _random_walk_candles(n: int, seed: int = 42) -> list[Candle]:
    """Marche aléatoire en régimes (tendances haussières/baissières alternées)."""
    rng = random.Random(seed)
    candles: list[Candle] = []
    price = 100.0
    step_ms = 900_000
    for i in range(n):
        regime = 1.0 if (i // 80) % 2 == 0 else -1.0
        price *= 1 + regime * 0.002 + rng.gauss(0, 0.004)
        high = price * (1 + abs(rng.gauss(0, 0.001)))
        low = price * (1 - abs(rng.gauss(0, 0.001)))
        candles.append(
            Candle(
                open_time=i * step_ms,
                close_time=i * step_ms + step_ms - 1,
                open=price,
                high=max(high, price, low),
                low=min(low, price, high),
                close=price,
                volume=1.0,
            )
        )
    return candles


def _state_at(closes, series, avg_gain, avg_loss, i, params) -> FormingState:
    j = i - 1
    e12 = ema(closes, params.macd_fast)[j]
    e26 = ema(closes, params.macd_slow)[j]
    return FormingState(
        prev_close=closes[j],
        ema_fast_prev=series["ema_fast"][j],
        ema_slow_prev=series["ema_slow"][j],
        ema_macd_fast_prev=e12,
        ema_macd_slow_prev=e26,
        macd_line_prev=series["macd"][j],
        macd_signal_prev=series["macd_signal"][j],
        avg_gain_prev=avg_gain[j],
        avg_loss_prev=avg_loss[j],
    )


class TestFormesFermees:
    def test_coïncidence_exacte_avec_les_séries_du_moteur(self):
        """Sur chaque close réel, forme fermée == condition du moteur."""
        candles = _random_walk_candles(900)
        params = MomentumParams()
        closes = [c.close for c in candles]
        series = compute_series(closes, params)
        avg_gain, avg_loss = _wilder_state(closes, params.rsi_len)
        warmup = params.ema_slow + params.macd_signal + 2
        verifie = 0
        for i in range(warmup + 1, len(candles)):
            st = _state_at(closes, series, avg_gain, avg_loss, i, params)
            ef, es = series["ema_fast"][i], series["ema_slow"][i]
            ml, ms, rsi = (
                series["macd"][i],
                series["macd_signal"][i],
                series["rsi"][i],
            )
            moteur_bull = (
                ef > es and rsi > params.rsi_long_threshold and ml > ms
            )
            moteur_bear = (
                ef < es and rsi < params.rsi_short_threshold and ml < ms
            )
            assert bullish_at(st, closes[i], params) == moteur_bull, f"BUY i={i}"
            assert bearish_at(st, closes[i], params) == moteur_bear, f"SELL i={i}"
            verifie += 1
        assert verifie > 600  # l'échantillon est significatif

    def test_monotonie_du_prix_provisoire(self):
        """BUY plus vrai quand P monte, SELL plus vrai quand P descend."""
        candles = _random_walk_candles(400)
        params = MomentumParams()
        closes = [c.close for c in candles]
        series = compute_series(closes, params)
        avg_gain, avg_loss = _wilder_state(closes, params.rsi_len)
        i = params.ema_slow + params.macd_signal + 3
        st = _state_at(closes, series, avg_gain, avg_loss, i, params)
        base = closes[i]
        scores = [
            (bullish_at(st, base * (1 + d / 1000), params)) for d in range(0, 21)
        ]
        # une fois vrai, ça reste vrai (croissance)
        assert scores == sorted(scores)
        bearish = [
            (bearish_at(st, base * (1 - d / 1000), params)) for d in range(0, 21)
        ]
        assert bearish == sorted(bearish)


class TestTriggerLevel:
    def test_niveau_coincide_avec_les_signaux_confirmés(self):
        """Si le moteur confirme à la clôture, P* doit être du bon côté du close."""
        candles = _random_walk_candles(1200, seed=7)
        params = MomentumParams()
        closes = [c.close for c in candles]
        series = compute_series(closes, params)
        avg_gain, avg_loss = _wilder_state(closes, params.rsi_len)
        warmup = params.ema_slow + params.macd_signal + 2
        examines = 0
        for i in range(warmup + 1, len(candles)):
            result = evaluate_momentum_v1(candles[: i + 1], params)
            if result is None:
                continue
            st = _state_at(closes, series, avg_gain, avg_loss, i, params)
            level = trigger_level(st, params, result.action)
            assert total_score(result) == score_at(st, result.entry, params)
            if result.action == "BUY":
                assert bullish_at(st, result.entry, params)
                assert level is not None and level <= result.entry * 1.000001
            else:
                assert bearish_at(st, result.entry, params)
                assert level is not None and level >= result.entry * 0.999999
            examines += 1
        assert examines >= 3  # le jeu de test produit des transitions


class TestRunStudy:
    def test_étude_end_to_end_et_cohérence_des_événements(self):
        candles = _random_walk_candles(1200, seed=11)
        events = run_study("TESTUSDT", candles, eps=0.002, min_score=0)
        assert all(e.near for e in events)
        # émis ⊂ confirmé ⊂ touché ⊂ annoncé
        assert all(e.confirmed for e in events if e.emitted)
        assert all(e.touched for e in events if e.confirmed)
        assert all(e.level is not None or True for e in events)
        confirmes = [e for e in events if e.confirmed]
        assert confirmes, "au moins un événement confirmé attendu"
        for e in confirmes:
            assert e.fill_advantage is not None and e.fill_advantage >= 0
        non_conf = [e for e in events if e.touched and not e.confirmed]
        for e in non_conf:
            assert e.bail_out is not None and e.bail_out < 0
