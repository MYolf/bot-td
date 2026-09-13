"""Macro Risk Engine — filtre de risque temporel macro (spec MACRO.md).

Filtre, jamais stratégie directionnelle : répond uniquement « est-ce un
mauvais moment pour laisser passer ce setup technique ? ».
"""

from engine.macro.calendar import (
    EVENT_TYPES,
    events_from_records,
    load_calendar,
    load_events,
)
from engine.macro.models import MacroContext, MacroEvent, MacroLevel
from engine.macro.risk_engine import MacroGate, WINDOWS, macro_context_at

__all__ = [
    "EVENT_TYPES",
    "MacroContext",
    "MacroEvent",
    "MacroGate",
    "MacroLevel",
    "WINDOWS",
    "events_from_records",
    "load_calendar",
    "load_events",
    "macro_context_at",
]
