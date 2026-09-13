"""Modèles du Macro Risk Engine (spec MACRO.md)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class MacroLevel(str, Enum):
    """Niveau de risque macro au moment d'un signal.

    UNKNOWN = données macro indisponibles/périmées/incohérentes : traité comme
    LOW par le moteur (failsafe, MACRO.md §7), mais distingué pour les logs
    et l'affichage.
    """

    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


@dataclass(frozen=True)
class MacroEvent:
    """Événement macroéconomique planifié (FOMC, CPI, NFP, PPI).

    ``scheduled_at`` est TOUJOURS en UTC (conversion depuis l'heure US faite
    une fois, à la génération du fichier — MACRO.md §5).
    """

    event_type: str
    scheduled_at: datetime  # tz-aware UTC
    source: str = "curated"

    @property
    def key(self) -> str:
        return f"{self.source}:{self.event_type}:{self.scheduled_at.isoformat()}"


@dataclass(frozen=True)
class MacroContext:
    """Réponse structurée du Macro Risk Engine pour un instant donné.

    ``minutes_to_event`` est signé : positif = avant l'événement, négatif =
    après. ``None`` quand aucun événement n'est à proximité.
    """

    level: MacroLevel
    event_type: str | None = None
    minutes_to_event: float | None = None
    note: str = ""
