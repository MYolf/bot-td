"""Chargement et validation du planning macro (fichier curated, MACRO.md §4).

Le fichier ``data/macro/events.json`` est versionné dans le repo : aucun appel
réseau au moment de la décision. Toute entrée incohérente est écartée avec un
log d'avertissement — jamais une exception (failsafe).

Format :

    [{"event_type": "CPI", "date": "2026-09-10", "time_et": "08:30"}, ...]

- ``date`` : ISO YYYY-MM-DD (jour de l'événement en heure US) ;
- ``time_et`` : optionnel, HH:MM en America/New_York — défaut = heure
  conventionnelle du type (MACRO.md §3). La conversion ET -> UTC gère
  l'heure d'été/hiver via zoneinfo.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from engine.macro.models import MacroEvent
from engine.macro.risk_engine import MacroGate

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Types suivis en v1 (MACRO.md §2). ISM : liste d'attente (Phase A).
EVENT_TYPES = frozenset({"FOMC", "CPI", "NFP", "PPI"})

# Heures conventionnelles US (MACRO.md §3) : type -> heure ET.
DEFAULT_TIME_ET: dict[str, time] = {
    "FOMC": time(14, 0),
    "CPI": time(8, 30),
    "NFP": time(8, 30),
    "PPI": time(8, 30),
}


def et_to_utc(day: date, hour_et: time) -> datetime:
    """Instant ET (avec DST du jour considéré) -> datetime UTC tz-aware.

    Point critique anti-décalage horaire : 8h30 ET = 12h30 UTC en juillet
    (EDT, UTC-4) mais 13h30 UTC en janvier (EST, UTC-5) — couvert par tests.
    """
    local = datetime.combine(day, hour_et, tzinfo=ET)
    return local.astimezone(timezone.utc)


def _parse_time_et(value: str) -> time | None:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def events_from_records(records: list, source: str = "curated") -> list[MacroEvent]:
    """Enregistrements bruts -> événements validés, dédupliqués, triés.

    Chaque entrée invalide (type inconnu, date/heure malformée) est écartée
    avec un log d'avertissement ; les doublons (type + instant) sont
    silencieusement fusionnés (première occurrence).
    """
    seen: set[str] = set()
    events: list[MacroEvent] = []
    for i, record in enumerate(records):
        if not isinstance(record, dict):
            logger.warning("macro: entrée %d ignorée (pas un objet)", i)
            continue
        raw_type = record.get("event_type")
        raw_date = record.get("date")
        if not isinstance(raw_type, str) or not isinstance(raw_date, str):
            logger.warning("macro: entrée %d ignorée (event_type/date manquants)", i)
            continue
        event_type = raw_type.strip().upper()
        if event_type not in EVENT_TYPES:
            logger.warning(
                "macro: entrée %d ignorée (type %r hors périmètre v1)", i, raw_type
            )
            continue
        try:
            day = date.fromisoformat(raw_date)
        except ValueError:
            logger.warning("macro: entrée %d ignorée (date invalide %r)", i, raw_date)
            continue
        raw_time = record.get("time_et", DEFAULT_TIME_ET[event_type])
        if isinstance(raw_time, str):
            parsed = _parse_time_et(raw_time)
            if parsed is None:
                logger.warning(
                    "macro: entrée %d ignorée (time_et invalide %r)", i, raw_time
                )
                continue
            hour_et = parsed
        else:
            hour_et = DEFAULT_TIME_ET[event_type]
        event = MacroEvent(
            event_type=event_type, scheduled_at=et_to_utc(day, hour_et), source=source
        )
        if event.key in seen:
            continue
        seen.add(event.key)
        events.append(event)
    events.sort(key=lambda e: e.scheduled_at)
    return events


def load_events(path: Path) -> list[MacroEvent]:
    """Événements depuis un fichier JSON ; liste vide si absent/invalide."""
    records = _read_records(path)
    return events_from_records(records)


def load_calendar(path: Path, types: frozenset[str] | set[str] | None = None) -> MacroGate:
    """Charge le planning et construit le gate.

    Fichier absent ou JSON invalide -> gate ``UNKNOWN`` (failsafe MACRO.md
    §7) : le moteur technique continue, la macro est déclarée indisponible.
    Fichier valide mais vide -> gate LOW (calendrier réellement silencieux).
    ``types`` : restreint le gate à ces types d'événements (display-only).
    """
    records = _read_records(path)
    if records is None:
        return MacroGate.disabled()
    return MacroGate(events_from_records(records), types)


def _read_records(path: Path) -> list | None:
    """Liste d'enregistrements bruts, ou None si fichier illisible."""
    if not path.exists():
        logger.warning("macro: fichier de planning absent (%s) -> UNKNOWN", path)
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("macro: planning illisible (%s) -> UNKNOWN", path)
        return None
    if not isinstance(payload, list):
        logger.warning("macro: planning invalide (racine non liste) -> UNKNOWN")
        return None
    return payload
