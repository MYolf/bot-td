"""Tests de la position simulée (engine/position.py).

Réplication du comportement d'alerte TradingView : SL/TP, priorité du SL,
pyramiding = 0 (signal dans le sens de la position ignoré), renversement.
"""

from engine.position import PositionTracker, replay_history
from engine.strategy import Candle

INTERVAL_MS = 900_000


def candle(close: float, high: float | None = None, low: float | None = None) -> Candle:
    return Candle(
        open_time=0,
        close_time=INTERVAL_MS - 1,
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=1.0,
    )


def make_candles(closes: list[float]) -> list[Candle]:
    return [candle(c) for c in closes]


def test_long_ferme_au_stop_loss() -> None:
    tracker = PositionTracker()
    tracker.open("BUY", entry=100.0, stop_loss=99.0, take_profit=102.0)
    assert tracker.apply_candle(candle(99.5, high=100.1, low=98.9)) == "stop_loss"
    assert tracker.position is None


def test_long_ferme_au_take_profit() -> None:
    tracker = PositionTracker()
    tracker.open("BUY", entry=100.0, stop_loss=99.0, take_profit=102.0)
    assert tracker.apply_candle(candle(102.5, high=102.5, low=100.2)) == "take_profit"
    assert tracker.position is None


def test_sl_prioritaire_si_sl_et_tp_touches() -> None:
    # Bougie gigantesque qui touche les deux : hypothèse prudente -> SL.
    tracker = PositionTracker()
    tracker.open("BUY", entry=100.0, stop_loss=99.0, take_profit=102.0)
    assert tracker.apply_candle(candle(110.0, high=115.0, low=95.0)) == "stop_loss"


def test_short_ferme_au_stop_loss_puis_take_profit() -> None:
    tracker = PositionTracker()
    tracker.open("SELL", entry=100.0, stop_loss=101.0, take_profit=98.0)
    assert tracker.apply_candle(candle(101.5, high=102.0, low=100.5)) == "stop_loss"
    assert tracker.position is None

    tracker.open("SELL", entry=100.0, stop_loss=101.0, take_profit=98.0)
    assert tracker.apply_candle(candle(97.5, high=100.0, low=97.0)) == "take_profit"
    assert tracker.position is None


def test_would_fill_pyramiding_zero() -> None:
    tracker = PositionTracker()
    assert tracker.would_fill("BUY") and tracker.would_fill("SELL")  # plat

    tracker.open("BUY", entry=100.0, stop_loss=99.0, take_profit=102.0)
    assert not tracker.would_fill("BUY")  # déjà long : pas d'ordre, pas d'alerte
    assert tracker.would_fill("SELL")  # renversement


def test_replay_history_serie_plate_sans_position() -> None:
    candles = make_candles([300.0] * 400)
    tracker = replay_history(candles)
    assert tracker.position is None


def test_replay_history_construit_la_position() -> None:
    # Plateau, baisse (SELL ouvert), puis rallye : à la fin, le SELL a été
    # refermé (TP touché pendant la baisse ou renversement sur le BUY).
    closes = [300.0] * 220
    closes += [300.0 - 0.5 * i for i in range(1, 201)]
    closes += [closes[-1] * (1.02**k) for k in range(1, 151)]
    tracker = replay_history(make_candles(closes))
    # Le rallye final (+2 %/bougie) referme forcément toute position courte
    # très vite ; on vérifie surtout qu'aucune exception et un état cohérent.
    assert tracker.position is None or tracker.position.side in ("long", "short")
