"""Tests de l'étude « SL élargi » (engine/sl_study.py, spec SL_ELARGI.md).

Vérifiés : grille figée (bracket multiplié, RR 1:2 conservé), transitions
IDENTIQUES entre multiplicateurs (seuls les niveaux de sortie changent),
fenêtres IS/OOS chronologiques disjointes avec warmup, exclusion des trades du
warmup, filtre min_score propagé.
"""

from __future__ import annotations

import pytest

from engine.sl_study import (
    MULTIPLIERS,
    evaluate_k,
    params_for,
    split_chrono,
)
from engine.strategy import Candle

TF_MS = 900_000  # 15 minutes
DAY_MS = 86_400_000


def make_candles(closes: list[float], start_ms: int = 0) -> list[Candle]:
    return [
        Candle(
            open_time=start_ms + i * TF_MS,
            close_time=start_ms + (i + 1) * TF_MS - 1,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1.0,
        )
        for i, close in enumerate(closes)
    ]


def serie_avec_transitions() -> list[Candle]:
    """Plateau (amorce) puis baisse (SELL) puis rallye (BUY) : 2 transitions."""
    closes = [300.0] * 220
    closes += [300.0 - 0.5 * i for i in range(1, 201)]
    closes += [closes[-1] * (1.02**k) for k in range(1, 151)]
    return make_candles(closes)


# ------------------------------------------------------------------ grille --


def test_grille_figee() -> None:
    assert MULTIPLIERS == (1, 2, 3, 4)


@pytest.mark.parametrize("k", MULTIPLIERS)
def test_params_for_bracket_multiplie_rr_constant(k: int) -> None:
    params = params_for(k)
    assert params.sl_pct == pytest.approx(0.01 * k)
    assert params.tp_pct == pytest.approx(0.02 * k)
    # RR 1:2 conservé quel que soit k.
    assert params.tp_pct / params.sl_pct == pytest.approx(2.0)


def test_transitions_identiques_niveaux_scalés() -> None:
    candles = serie_avec_transitions()
    from engine.momentum_study import all_transitions

    refs = all_transitions(candles, params_for(1))
    assert len(refs) >= 2  # SELL puis BUY
    for k in MULTIPLIERS:
        results = all_transitions(candles, params_for(k))
        assert len(results) == len(refs)
        for ref, res in zip(refs, results):
            # Même transition : action, entrée, bougie, scores inchangés.
            assert res.action == ref.action
            assert res.entry == ref.entry
            assert res.candle_open_time == ref.candle_open_time
            assert res.score_trend == ref.score_trend
            # Seuls les niveaux de sortie s'écartent, d'un facteur k exact.
            risk_ref = abs(ref.entry - ref.stop_loss)
            risk_k = abs(res.entry - res.stop_loss)
            reward_k = abs(res.take_profit - res.entry)
            assert risk_k == pytest.approx(k * risk_ref)
            assert reward_k == pytest.approx(2.0 * risk_k)


# --------------------------------------------------------------- fenêtres --


def test_split_chrono_disjoint_avec_warmup() -> None:
    # 10 jours de bougies 15m.
    n = 96 * 10
    candles = make_candles([100.0] * n)
    is_part, oos_part, oos_start = split_chrono(candles, oos_days=4, warmup_days=1)
    end_ms = candles[-1].close_time + 1
    assert oos_start == end_ms - 4 * DAY_MS
    # IS : tout ce qui précède oos_start (aucune bougie commune).
    assert all(c.close_time < oos_start for c in is_part)
    assert is_part[-1].close_time < oos_start
    # OOS : fenêtre utile + exactement 1 jour de warmup (paramètre warmup_days).
    assert oos_start - DAY_MS <= oos_part[0].open_time < oos_start - DAY_MS + TF_MS
    assert all(c.open_time < end_ms for c in oos_part)


def test_split_chrono_historique_trop_court() -> None:
    candles = make_candles([100.0] * 10)
    with pytest.raises(ValueError):
        split_chrono(candles, oos_days=4, warmup_days=1)
    with pytest.raises(ValueError):
        split_chrono([], oos_days=4)


# ------------------------------------------------------------- évaluation --


def test_evaluate_k_warmup_exclu_et_min_score_propage() -> None:
    candles = serie_avec_transitions()
    # Sans fenêtre : tous les trades (ici 2 transitions, renversement possible).
    tous = evaluate_k(candles, 1, min_score=0, fee_rate=0.0)
    assert len(tous) >= 1
    # Fenêtre utile après le dernier trade : tous les trades du warmup sautent.
    derniere = candles[tous[-1].open_index].open_time
    assert evaluate_k(candles, 1, 0, 0.0, derniere + TF_MS) == []
    premiere = candles[tous[0].open_index].open_time
    assert evaluate_k(candles, 1, 0, 0.0, premiere) == tous
    # Filtre min_score=100 (max 55) : plus aucune entrée simulée.
    assert evaluate_k(candles, 1, 100, 0.0) == []


def test_evaluate_k_plus_k_grand_plus_frais_dilués() -> None:
    """Le coût des frais en R est divisé par k quand le bracket est ×k."""
    candles = serie_avec_transitions()
    taker = 0.0012
    for k in MULTIPLIERS:
        brut = evaluate_k(candles, k, 0, 0.0)
        fees = evaluate_k(candles, k, 0, taker)
        assert len(brut) == len(fees) and len(brut) >= 1
        cout_ref = brut[0].raw_r - fees[0].net_r
        if k == 1:
            cout_k1 = cout_ref
        assert pytest.approx(cout_ref, rel=0.01) == cout_k1 / k
