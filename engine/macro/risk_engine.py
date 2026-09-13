"""Cœur du Macro Risk Engine : niveau de risque macro pour un instant donné.

Fonction pure, sans I/O, sans horloge : ``macro_context_at(events, ts)`` ne
dépend QUE du planning fourni et du timestamp demandé (borne de clôture de
bougie en production). Le backtest et la production exécutent donc
exactement le même code — lookahead impossible par construction (MACRO.md
§6) puisque seule ``scheduled_at``, connue des semaines à l'avance, est
utilisée.

Fenêtres a priori figées (MACRO.md §3). Pour chaque niveau, (pre, post) en
minutes POSITIVES autour de l'annonce T :

    EXTREME : -extreme_post <= minutes_to <= extreme_pre
    HIGH    : -high_post    <= minutes_to <= high_pre

avec minutes_to = (scheduled_at - ts) / 60 s (positif = avant l'événement).
Exemple CPI (15, 30) : EXTREME de T-15 à T+30 inclus.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from engine.macro.models import MacroContext, MacroEvent, MacroLevel

# type -> (extreme_pre, extreme_post, high_pre, high_post), minutes positives.
WINDOWS: dict[str, tuple[int, int, int, int]] = {
    # T-15 -> T+45 : la conférence de presse prolonge la fenêtre.
    "FOMC": (15, 45, 60, 120),
    # Annonces 8h30 ET : choc bref, absorption plus rapide.
    "CPI": (15, 30, 30, 60),
    "NFP": (15, 30, 30, 60),
    "PPI": (15, 30, 30, 60),
}

_LEVEL_ORDER = {
    MacroLevel.LOW: 0,
    MacroLevel.HIGH: 1,
    MacroLevel.EXTREME: 2,
}


def _level_for(event_type: str, minutes_to: float) -> MacroLevel:
    """Niveau induit par UN événement à ``minutes_to`` de l'instant demandé."""
    windows = WINDOWS.get(event_type)
    if windows is None:
        return MacroLevel.LOW
    extreme_pre, extreme_post, high_pre, high_post = windows
    if -extreme_post <= minutes_to <= extreme_pre:
        return MacroLevel.EXTREME
    if -high_post <= minutes_to <= high_pre:
        return MacroLevel.HIGH
    return MacroLevel.LOW


def _note(event_type: str, minutes_to: float) -> str:
    minutes = round(minutes_to)
    if minutes >= 0:
        return f"{event_type} dans {minutes} min"
    return f"{event_type} il y a {-minutes} min"


def macro_context_at(events: list[MacroEvent], ts: datetime) -> MacroContext:
    """Contexte macro au plus près d'un instant (pire événement retenu).

    ``ts`` doit être tz-aware (UTC en pratique : borne de clôture de bougie).
    Plusieurs événements proches : niveau maximum, puis distance minimale.
    """
    if ts.tzinfo is None:
        raise ValueError("ts doit être tz-aware (UTC attendu)")
    best: MacroContext | None = None
    for event in events:
        minutes_to = (event.scheduled_at - ts).total_seconds() / 60
        level = _level_for(event.event_type, minutes_to)
        if level is MacroLevel.LOW:
            continue
        better = (
            best is None
            or _LEVEL_ORDER[level] > _LEVEL_ORDER[best.level]
            or (
                _LEVEL_ORDER[level] == _LEVEL_ORDER[best.level]
                and abs(minutes_to) < abs(best.minutes_to_event or 0.0)
            )
        )
        if better:
            best = MacroContext(
                level=level,
                event_type=event.event_type,
                minutes_to_event=minutes_to,
                note=_note(event.event_type, minutes_to),
            )
    if best is not None:
        return best
    return MacroContext(level=MacroLevel.LOW)


class MacroGate:
    """Gate injectable dans SignalEngine (pattern Fetcher/Sender).

    - ``MacroGate(events)`` : décision réelle (fonction pure ci-dessus) ;
    - ``MacroGate.disabled()`` : planning indisponible -> UNKNOWN partout
      (failsafe) ; momentum_v1 continue sans aucune information macro.
    """

    def __init__(self, events: list[MacroEvent] | None) -> None:
        self._events = events

    @classmethod
    def disabled(cls) -> MacroGate:
        return cls(events=None)

    def context_at(self, ts: datetime) -> MacroContext:
        if self._events is None:
            return MacroContext(level=MacroLevel.UNKNOWN)
        return macro_context_at(self._events, ts.astimezone(timezone.utc))
