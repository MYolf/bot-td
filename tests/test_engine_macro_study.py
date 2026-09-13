"""Tests de l'event study macro (Phase A — gate, MACRO.md §8).

Vérifiés sur données synthétiques : appartenance des bougies aux fenêtres
(bornes demi-ouvertes, bougie 1H CONTENANT l'événement dans le cœur),
baseline = même jour de semaine + même minute de journée hors jours
d'événement, ratios médians, verdict du gate scellé.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from engine.macro.models import MacroEvent
from engine.macro_study import (
    GATE_SYMBOLS,
    GATE_TIMEFRAME,
    GATE_TYPES,
    baseline_indices,
    candle_in_window,
    gate_pass,
    study_type,
    window_indices,
)
from engine.strategy import Candle

UTC = timezone.utc
TF_MS = 900_000  # 15 min

# Mercredi connu : 2026-07-08 (testé par construction ci-dessous).
_BASE_WEDNESDAY = date(2026, 7, 8)
assert _BASE_WEDNESDAY.weekday() == 2  # mercredi

_EVENT_AT = datetime(2026, 7, 8, 12, 30, tzinfo=UTC)  # CPI (8h30 EDT)
_CPI = MacroEvent(event_type="CPI", scheduled_at=_EVENT_AT)


def _candle(dt: datetime, range_frac: float, volume: float = 10.0) -> Candle:
    t = int(dt.timestamp() * 1000)
    open_ = 100.0
    return Candle(
        open_time=t,
        close_time=t + TF_MS - 1,
        open=open_,
        high=open_ * (1 + range_frac / 2),
        low=open_ * (1 - range_frac / 2),
        close=open_,
        volume=volume,
    )


def _wednesday_candles(day: date, range_frac: float) -> list[Candle]:
    """16 bougies 15m de 11:00 à 14:45 UTC autour d'un hypothétique 12:30."""
    out = []
    moment = datetime(day.year, day.month, day.day, 11, 0, tzinfo=UTC)
    for _ in range(16):
        out.append(_candle(moment, range_frac))
        moment += timedelta(minutes=15)
    return out


def _series(
    n_weeks: int = 6,
    event_week: int = 3,
    event_range: float = 0.04,
    base_range: float = 0.01,
) -> tuple[list[Candle], MacroEvent]:
    """n_weeks mercredis ; la semaine ``event_week`` porte le CPI avec une
    amplitude event_range, les autres servent de baseline (base_range)."""
    candles: list[Candle] = []
    for w in range(n_weeks):
        day = _BASE_WEDNESDAY + timedelta(weeks=w - event_week)
        candles.extend(
            _wednesday_candles(day, event_range if w == event_week else base_range)
        )
    return candles, _CPI


# ------------------------------------------------------------- fenêtres --


def test_appartenance_aux_fenetres_bornes_exactes() -> None:
    event_ms = int(_EVENT_AT.timestamp() * 1000)
    candles = _wednesday_candles(_BASE_WEDNESDAY, 0.01)

    def times(indices: list[int]) -> set[str]:
        return {
            datetime.fromtimestamp(candles[i].open_time / 1000, tz=UTC).strftime("%H:%M")
            for i in indices
        }

    # pré [11:30, 12:15) : 11:15 exclue (bougie fermée avant la borne),
    # 12:15 exclue (ouverte À la borne de fin).
    pre, _ = window_indices(candles, [_CPI], (-60, -15))
    assert times(pre) == {"11:30", "11:45", "12:00"}
    # cœur [12:15, 13:00) : 12:00 exclue (fermée avant la borne),
    # 13:00 exclue (ouverte à la borne de fin).
    core, _ = window_indices(candles, [_CPI], (-15, 30))
    assert times(core) == {"12:15", "12:30", "12:45"}
    post, _ = window_indices(candles, [_CPI], (30, 120))
    # post [13:00, 14:30) : 14:30 exclue (ouverte à la borne de fin).
    assert times(post) == {"13:00", "13:15", "13:30", "13:45", "14:00", "14:15"}


def test_bougie_1h_contenant_evenement_dans_le_coeur() -> None:
    # Une bougie 1H ouverte à 12:00 contient l'événement 12:30 -> cœur.
    t = int(datetime(2026, 7, 8, 12, 0, tzinfo=UTC).timestamp() * 1000)
    hourly = Candle(t, t + 3_600_000 - 1, 100, 101, 99, 100, 10)
    event_ms = int(_EVENT_AT.timestamp() * 1000)
    assert candle_in_window(hourly, event_ms, -15, 30)
    # Bougie 1H ouverte à 13:00 (borne de fin) -> hors cœur.
    t2 = int(datetime(2026, 7, 8, 13, 0, tzinfo=UTC).timestamp() * 1000)
    after = Candle(t2, t2 + 3_600_000 - 1, 100, 101, 99, 100, 10)
    assert not candle_in_window(after, event_ms, -15, 30)


# -------------------------------------------------------------- baseline --


def test_baseline_meme_jour_semaine_hors_jours_evenement() -> None:
    candles, event = _series()
    refs, _ = window_indices(candles, [event], (-15, 30))
    base = baseline_indices(candles, refs, {event.scheduled_at.date()})
    assert len(refs) == 3  # 3 bougies du jour événement
    assert len(base) == 15  # 5 mercredis x 3 minutes de journée
    for i in base:
        moment = datetime.fromtimestamp(candles[i].open_time / 1000, tz=UTC)
        assert moment.weekday() == 2  # mercredi uniquement
        assert moment.date() != _BASE_WEDNESDAY  # jamais le jour événement
        assert moment.strftime("%H:%M") in {"12:15", "12:30", "12:45"}


def test_baseline_ignore_les_autres_jours_de_la_semaine() -> None:
    # Un jeudi avec les mêmes minutes ne doit PAS entrer dans la baseline.
    candles, event = _series()
    candles = candles + _wednesday_candles(
        _BASE_WEDNESDAY + timedelta(days=1), 0.01
    )
    refs, _ = window_indices(candles, [event], (-15, 30))
    base = baseline_indices(candles, refs, {event.scheduled_at.date()})
    moments = [
        datetime.fromtimestamp(candles[i].open_time / 1000, tz=UTC) for i in base
    ]
    assert all(m.weekday() == 2 for m in moments)


# ----------------------------------------------------------------- étude --


def test_study_type_ratios_medians() -> None:
    candles, event = _series(event_range=0.04, base_range=0.01)
    study = study_type(candles, [event], "CPI")
    assert study["n_events"] == 1
    core = study["core"]
    assert core["n_candles"] == 3
    assert core["n_baseline"] == 15
    assert core["ratio_range"] == pytest.approx(4.0)


def test_study_type_ignore_evenements_hors_historique() -> None:
    candles, _ = _series()
    hors_range = MacroEvent(
        event_type="CPI", scheduled_at=datetime(2020, 1, 10, 13, 30, tzinfo=UTC)
    )
    study = study_type(candles, [hors_range], "CPI")
    assert study["n_events"] == 0
    assert study["core"]["ratio_range"] is None


# ------------------------------------------------------------------ gate --


def _combo(symbol: str, ratio: float | None) -> dict:
    return {"n_events": 10, "core": {"ratio_range": ratio}}


def test_gate_pass_les_4_combinaisons() -> None:
    studies = {
        **{(s, GATE_TIMEFRAME, t): _combo(s, 2.5) for s in GATE_SYMBOLS for t in GATE_TYPES},
    }
    ok, details = gate_pass(studies)
    assert ok
    assert len(details) == 4
    assert all("PASS" in line for line in details)


def test_gate_fail_si_une_combinaison_sous_le_seuil() -> None:
    studies = {
        (s, GATE_TIMEFRAME, t): _combo(s, 2.5)
        for s in GATE_SYMBOLS
        for t in GATE_TYPES
    }
    studies[("ETHUSDT", GATE_TIMEFRAME, "CPI")] = _combo("ETHUSDT", 1.9)
    ok, details = gate_pass(studies)
    assert not ok
    assert sum("FAIL" in line for line in details) == 1


def test_gate_fail_si_ratio_indisponible() -> None:
    studies = {
        (s, GATE_TIMEFRAME, t): _combo(s, 2.5)
        for s in GATE_SYMBOLS
        for t in GATE_TYPES
    }
    studies[("BTCUSDT", GATE_TIMEFRAME, "FOMC")] = _combo("BTCUSDT", None)
    ok, _ = gate_pass(studies)
    assert not ok


def test_gate_fail_si_combinaison_manquante() -> None:
    ok, _ = gate_pass({})
    assert not ok
