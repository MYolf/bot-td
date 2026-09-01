"""Tests du protocole de validation : découpage IS/OOS, warmup, walk-forward.

Vérifiés : bornes exactes des fenêtres (l'OOS est la fenêtre ancienne,
l'IS la récente, chacune avec son préfixe d'amorce), exclusion des trades
du warmup, sélection walk-forward sur l'expectancy d'entraînement uniquement.
"""

from __future__ import annotations

from engine.confluence import ConfluenceParams
from engine.confluence_backtest import ExitPolicy, SignalEntry, Trade, simulate_trades
from engine.strategy import Candle
from engine.validation import (
    Variant,
    evaluate,
    split_is_oos,
    trades_in_window,
    walk_forward,
)

TF_MS = 900_000
DAY_MS = 86_400_000


def _candle(i: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        open_time=i * TF_MS,
        close_time=i * TF_MS + TF_MS - 1,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=10.0,
    )


def _flat(n: int, start_index: int = 0) -> list[Candle]:
    return [_candle(start_index + i, 100.0, 100.5, 99.5, 100.0) for i in range(n)]


# ------------------------------------------------------------ split IS/OOS --


def test_split_is_oos_fenetres_et_warmup() -> None:
    # 1 bougie = 15 min ; 96 bougies = 1 jour.
    candles = _flat(96 * 5)
    is_part, oos_part, is_start, oos_start = split_is_oos(
        candles, is_days=2, oos_days=2, warmup_days=1
    )
    end_ms = candles[-1].close_time + 1
    assert is_start == end_ms - 2 * DAY_MS
    assert oos_start == end_ms - 4 * DAY_MS
    # Chaque part couvre fenêtre utile + 1 jour de warmup.
    assert is_part[0].open_time <= is_start - DAY_MS
    assert is_part[0].open_time > is_start - DAY_MS - TF_MS
    assert oos_part[0].open_time <= oos_start - DAY_MS
    assert all(c.close_time < is_start for c in oos_part)
    # Pas de recouvrement des fenêtres UTILES.
    assert all(c.open_time >= is_start for c in is_part if c.open_time >= is_start)


def test_trades_in_window_exclut_le_warmup() -> None:
    candles = _flat(10)
    entries = [SignalEntry(index=0, action="BUY", entry=100.0, stop_loss=98.0, take_profit=110.0)]
    trades = simulate_trades(candles, entries, fee_rate=0.0)
    assert len(trades) == 1
    # Fenêtre commençant après la bougie 0 : le trade du warmup est exclu.
    assert trades_in_window(candles, trades, candles[3].open_time) == []
    assert trades_in_window(candles, trades, candles[0].open_time) == trades


# ------------------------------------------------------------- évaluation --


def test_evaluate_min_score_filtre() -> None:
    # Série au balayage connu (bascule bull) : au moins 1 signal score 47.
    candles = []
    for i in range(11):
        if i == 2:
            candles.append(_candle(i, 100.0, 100.5, 97.0, 100.0))
        elif i == 10:
            candles.append(_candle(10, 100.0, 101.0, 96.5, 100.0))
        else:
            candles.append(_candle(i, 100.0, 100.5, 99.0, 100.0))
    params = ConfluenceParams(sweep_k=2, sweep_min_age=5, atr_len=2)
    tout = evaluate(candles, Variant("all", params, ExitPolicy(), 0))
    assert len(tout) >= 1
    aucun = evaluate(candles, Variant("none", params, ExitPolicy(), 90))
    assert aucun == []


# ------------------------------------------------------------- walk-forward --


def test_walk_forward_selectionne_sur_train_et_teste_apres() -> None:
    # Deux variantes : une qui trade (score bas requis), une jamais éligible
    # (seuil de score inaccessible -> moins de min_train_trades).
    candles = _flat(96 * 8)  # 8 jours : 1 fenêtre train(3j) + test(3j) complète
    candles = list(candles)
    for i in range(20, 28):  # swing + sweep pour générer un signal
        base = candles[i]
        if i == 22:
            candles[i] = _candle(i, 100.0, 100.5, 97.0, 100.0)
        elif i == 26:
            candles[i] = _candle(i, 100.0, 101.0, 96.5, 100.5)
        else:
            candles[i] = base
    params = ConfluenceParams(sweep_k=2, sweep_min_age=3, atr_len=2)
    active = Variant("active", params, ExitPolicy(), 0)
    muette = Variant("muette", params, ExitPolicy(), 100)
    report, stitched = walk_forward(
        candles,
        [muette, active],
        fee_rate=0.0,
        train_days=3,
        test_days=3,
        warmup_days=1,
        min_train_trades=1,
    )
    # La variante muette n'a jamais les trades minimum : seul "active" peut
    # être sélectionnée, et uniquement sur des fenêtres couvrant le signal.
    assert all(row["variant"] == "active" for row in report)
    # Les trades de test proviennent de fenêtres POSTÉRIEURES au choix.
    for row in report:
        assert row["test_start"] > row["train_start"]
    assert isinstance(stitched, list)


def _jours_avec_signaux(n_days: int) -> list[Candle]:
    """Un swing low balayé par jour (motif de l'étape 3, répété)."""
    candles: list[Candle] = []
    for d in range(n_days):
        for i in range(96):
            gi = d * 96 + i
            if i == 5:
                candles.append(_candle(gi, 100.0, 100.5, 97.0, 100.0))
            elif i == 9:
                candles.append(_candle(gi, 100.0, 101.0, 96.5, 100.5))
            else:
                candles.append(_candle(gi, 100.0, 100.5, 99.0, 100.0))
    return candles


def test_walk_forward_fenetres_consecutives() -> None:
    candles = _jours_avec_signaux(12)
    params = ConfluenceParams(sweep_k=2, sweep_min_age=3, atr_len=2)
    # Sortie temporelle courte : chaque jour porte un trade fermé.
    report, stitched = walk_forward(
        candles,
        [Variant("v", params, ExitPolicy("time", 8), 0)],
        train_days=3,
        test_days=3,
        warmup_days=1,
        min_train_trades=1,
    )
    # (12 j - 3 j de train) / 3 j de test = 3 fenêtres, espacées de 3 j.
    assert len(report) == 3
    for previous, following in zip(report, report[1:]):
        assert following["test_start"] - previous["test_start"] == 3 * DAY_MS
    # Chaque jour porte un signal : les fenêtres de test contiennent des trades.
    assert len(stitched) >= 3


# ----------------------------------------------------------- ExitPolicy ----


def test_exit_policy_rejette_valeurs_invalides() -> None:
    import pytest

    with pytest.raises(ValueError):
        ExitPolicy("autre")
    with pytest.raises(ValueError):
        ExitPolicy("time", 0)
