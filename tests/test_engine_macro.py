"""Tests du Macro Risk Engine : calendrier, fuseaux horaires, niveaux.

Points critiques couverts (MACRO.md §5-§7) :
- conversion ET -> UTC avec heure d'été/hiver (8h30 ET = 12h30 UTC en juillet,
  13h30 UTC en janvier — AUCUN décalage ne doit passer inaperçu) ;
- bornes exactes des fenêtres par type d'événement ;
- failsafe : fichier absent/invalide -> UNKNOWN, entrées invalides écartées ;
- anti-lookahead : la fonction pure ne dépend que du planning et du timestamp.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from engine.macro.calendar import (
    et_to_utc,
    events_from_records,
    load_calendar,
)
from engine.macro.models import MacroEvent, MacroLevel
from engine.macro.risk_engine import MacroGate, macro_context_at

UTC = timezone.utc


# ------------------------------------------------------------ fuseaux --


def test_et_vers_utc_ete_vs_hiver() -> None:
    # 8h30 ET : EDT (UTC-4) en juillet, EST (UTC-5) en janvier.
    assert et_to_utc(date(2026, 7, 10), time(8, 30)) == datetime(
        2026, 7, 10, 12, 30, tzinfo=UTC
    )
    assert et_to_utc(date(2026, 1, 10), time(8, 30)) == datetime(
        2026, 1, 10, 13, 30, tzinfo=UTC
    )
    # FOMC 14h00 ET : 18h00 UTC en été, 19h00 UTC en hiver.
    assert et_to_utc(date(2026, 7, 29), time(14, 0)) == datetime(
        2026, 7, 29, 18, 0, tzinfo=UTC
    )
    assert et_to_utc(date(2026, 1, 28), time(14, 0)) == datetime(
        2026, 1, 28, 19, 0, tzinfo=UTC
    )


# ----------------------------------------------------------- calendrier --


def test_heure_conventionnelle_par_defaut() -> None:
    events = events_from_records(
        [
            {"event_type": "CPI", "date": "2026-07-10"},  # 8h30 ET par défaut
            {"event_type": "FOMC", "date": "2026-07-29"},  # 14h00 ET par défaut
        ]
    )
    assert [e.scheduled_at for e in events] == [
        datetime(2026, 7, 10, 12, 30, tzinfo=UTC),
        datetime(2026, 7, 29, 18, 0, tzinfo=UTC),
    ]


def test_type_normalise_et_time_et_explicite() -> None:
    events = events_from_records(
        [{"event_type": "cpi", "date": "2026-03-18", "time_et": "08:30"}]
    )
    assert len(events) == 1
    assert events[0].event_type == "CPI"
    # 18 mars 2026 : DST déjà active (2e dimanche de mars) -> 12h30 UTC.
    assert events[0].scheduled_at == datetime(2026, 3, 18, 12, 30, tzinfo=UTC)


def test_entrees_invalides_ecartees() -> None:
    events = events_from_records(
        [
            {"event_type": "ISM", "date": "2026-01-10"},  # hors périmètre v1
            {"event_type": "CPI", "date": "pas-une-date"},
            {"event_type": "CPI", "date": "2026-01-10", "time_et": "25:99"},
            "pas-un-dict",
            {"event_type": "CPI"},  # date manquante
            {"event_type": "CPI", "date": "2026-01-10"},  # valide
        ]
    )
    assert len(events) == 1
    assert events[0].event_type == "CPI"


def test_doublons_fusionnes_et_tri_chronologique() -> None:
    events = events_from_records(
        [
            {"event_type": "NFP", "date": "2026-07-03"},
            {"event_type": "FOMC", "date": "2026-07-29"},
            {"event_type": "NFP", "date": "2026-07-03"},  # doublon exact
            {"event_type": "NFP", "date": "2026-07-03", "time_et": "08:30"},  # doublon
        ]
    )
    assert len(events) == 2
    assert events[0].event_type == "NFP"
    assert events[1].event_type == "FOMC"


def test_load_calendar_fichier_absent_unknown(tmp_path) -> None:
    gate = load_calendar(tmp_path / "inexistant.json")
    context = gate.context_at(datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
    assert context.level is MacroLevel.UNKNOWN
    assert context.event_type is None


def test_load_calendar_json_invalide_unknown(tmp_path) -> None:
    path = tmp_path / "events.json"
    path.write_text("{pas du json", encoding="utf-8")
    gate = load_calendar(path)
    assert gate.context_at(datetime(2026, 7, 10, 12, 0, tzinfo=UTC)).level is MacroLevel.UNKNOWN


def test_load_calendar_racine_non_liste_unknown(tmp_path) -> None:
    path = tmp_path / "events.json"
    path.write_text('{"event_type": "CPI"}', encoding="utf-8")
    gate = load_calendar(path)
    assert gate.context_at(datetime(2026, 7, 10, 12, 0, tzinfo=UTC)).level is MacroLevel.UNKNOWN


def test_load_calendar_fichier_vide_low(tmp_path) -> None:
    path = tmp_path / "events.json"
    path.write_text("[]", encoding="utf-8")
    gate = load_calendar(path)
    assert gate.context_at(datetime(2026, 7, 10, 12, 0, tzinfo=UTC)).level is MacroLevel.LOW


# -------------------------------------------------------- niveaux/fenêtres --

_CPI = MacroEvent(
    event_type="CPI", scheduled_at=datetime(2026, 7, 10, 12, 30, tzinfo=UTC)
)
_FOMC = MacroEvent(
    event_type="FOMC", scheduled_at=datetime(2026, 7, 29, 18, 0, tzinfo=UTC)
)


def _at(event: MacroEvent, minutes: float) -> MacroLevel:
    ts = event.scheduled_at.fromtimestamp(
        event.scheduled_at.timestamp() - minutes * 60, tz=UTC
    )
    return macro_context_at([event], ts).level


def test_fenetres_cpi() -> None:
    # EXTREME T-15 -> T+30, HIGH T-30 -> T+60.
    assert _at(_CPI, 8) is MacroLevel.EXTREME
    assert _at(_CPI, 15) is MacroLevel.EXTREME  # borne incluse
    assert _at(_CPI, 20) is MacroLevel.HIGH
    assert _at(_CPI, 30) is MacroLevel.HIGH  # borne incluse
    assert _at(_CPI, 45) is MacroLevel.LOW
    assert _at(_CPI, 0) is MacroLevel.EXTREME
    assert _at(_CPI, -30) is MacroLevel.EXTREME  # T+30 inclus
    assert _at(_CPI, -35) is MacroLevel.HIGH
    assert _at(_CPI, -60) is MacroLevel.HIGH
    assert _at(_CPI, -65) is MacroLevel.LOW


def test_fenetres_fomc() -> None:
    # EXTREME T-15 -> T+45 (conférence de presse), HIGH T-60 -> T+120.
    assert _at(_FOMC, 10) is MacroLevel.EXTREME
    assert _at(_FOMC, 20) is MacroLevel.HIGH
    assert _at(_FOMC, 60) is MacroLevel.HIGH
    assert _at(_FOMC, 70) is MacroLevel.LOW
    assert _at(_FOMC, -40) is MacroLevel.EXTREME
    assert _at(_FOMC, -45) is MacroLevel.EXTREME
    assert _at(_FOMC, -50) is MacroLevel.HIGH
    assert _at(_FOMC, -120) is MacroLevel.HIGH
    assert _at(_FOMC, -130) is MacroLevel.LOW


def test_note_lisible() -> None:
    ts = datetime(2026, 7, 10, 12, 22, tzinfo=UTC)
    context = macro_context_at([_CPI], ts)
    assert context.level is MacroLevel.EXTREME
    assert context.note == "CPI dans 8 min"
    ts_apres = datetime(2026, 7, 10, 12, 40, tzinfo=UTC)
    assert macro_context_at([_CPI], ts_apres).note == "CPI il y a 10 min"


def test_evenement_lointain_low() -> None:
    ts = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    context = macro_context_at([_CPI], ts)
    assert context.level is MacroLevel.LOW
    assert context.event_type is None
    assert context.minutes_to_event is None


def test_plus_pire_evenement_retenu() -> None:
    # CPI imminent (EXTREME) + FOMC 55 min plus tard (HIGH depuis ce ts).
    fomc = MacroEvent(
        event_type="FOMC",
        scheduled_at=datetime(2026, 7, 10, 13, 25, tzinfo=UTC),
    )
    ts = datetime(2026, 7, 10, 12, 22, tzinfo=UTC)
    context = macro_context_at([fomc, _CPI], ts)
    assert context.level is MacroLevel.EXTREME
    assert context.event_type == "CPI"


def test_ts_naive_refusee() -> None:
    with pytest.raises(ValueError):
        macro_context_at([_CPI], datetime(2026, 7, 10, 12, 22))


def test_gate_disabled_unknown() -> None:
    gate = MacroGate.disabled()
    assert gate.context_at(datetime(2026, 7, 10, 12, 22, tzinfo=UTC)).level is MacroLevel.UNKNOWN


def test_purete_sans_mutation() -> None:
    # Anti-lookahead : le planning ne peut pas être modifié par l'appel.
    ts = datetime(2026, 7, 10, 12, 22, tzinfo=UTC)
    snapshot = list([_CPI])
    macro_context_at([_CPI], ts)
    assert snapshot == [_CPI]
