"""Tests du générateur de planning macro (FRED + FOMC curated).

Le client HTTP est factice : aucune requête réelle. Vérifiés : résolution
dynamique des release_id par nom, extraction des dates, fusion/tri/filtre/
déduplication des enregistrements, heures volontairement omises (appliquées
par calendar.py, source unique de vérité).
"""

from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest

from engine.macro.calendar import events_from_records
from engine.macro.generate import (
    FOMC_DECISION_DATES,
    RELEASE_NAMES,
    build_records,
    fetch_release_dates,
    resolve_release_id,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """Routes URL -> payload JSON. Enregistre les appels pour les assertions."""

    def __init__(self, routes: dict[str, dict]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    async def get(self, url: str, params: dict | None = None) -> FakeResponse:
        self.calls.append((url, dict(params) if params else None))
        if url not in self.routes:
            raise LookupError(f"route inattendue : {url}")
        return FakeResponse(self.routes[url])


_RELEASES_URL = "https://api.stlouisfed.org/fred/releases"
_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"

_RELEASES_PAYLOAD = {
    "releases": [
        {"id": 9, "name": "Employment Cost Index"},
        {"id": 10, "name": "Consumer Price Index"},
        {"id": 50, "name": "Employment Situation"},
        {"id": 46, "name": "Producer Price Index"},
        {"id": 99, "name": "Consumer Price Index for All Urban Consumers: Supplemental"},
    ]
}


# ------------------------------------------------------------ résolution --


def test_resolve_release_id_par_nom_exact() -> None:
    client = FakeClient({_RELEASES_URL: _RELEASES_PAYLOAD})
    release_id = asyncio.run(resolve_release_id(client, "k", "Employment Situation"))
    assert release_id == 50
    # La clé API ne doit jamais fuiter dans les logs — elle transite par params.
    assert client.calls[0][1]["api_key"] == "k"


def test_resolve_release_id_prefixe() -> None:
    client = FakeClient({_RELEASES_URL: _RELEASES_PAYLOAD})
    # Pas de nom exact, mais un préfixe unique.
    release_id = asyncio.run(
        resolve_release_id(client, "k", "Consumer Price Index for")
    )
    assert release_id == 99


def test_resolve_release_id_introuvable() -> None:
    client = FakeClient({_RELEASES_URL: _RELEASES_PAYLOAD})
    with pytest.raises(LookupError):
        asyncio.run(resolve_release_id(client, "k", "Inexistant"))


def test_toutes_les_releases_v1_sont_resolvables() -> None:
    client = FakeClient({_RELEASES_URL: _RELEASES_PAYLOAD})
    for name in RELEASE_NAMES.values():
        release_id = asyncio.run(resolve_release_id(client, "k", name))
        assert isinstance(release_id, int)


# ----------------------------------------------------------------- dates --


def test_fetch_release_dates() -> None:
    client = FakeClient(
        {
            _DATES_URL: {
                "release_dates": [
                    {"release_id": 10, "date": "2026-09-10"},
                    {"release_id": 10, "date": "2026-10-01"},
                    {"release_id": 10},  # entrée incomplète ignorée
                ]
            }
        }
    )
    dates = asyncio.run(fetch_release_dates(client, "k", 10))
    assert dates == ["2026-09-10", "2026-10-01"]


# --------------------------------------------------------------- fusion --


def test_build_records_fusionne_trie_filtre_deduplique() -> None:
    records = build_records(
        fred_dates={
            "CPI": ["2026-09-10", "2026-08-12", "2026-08-12"],  # doublon
            "NFP": ["2026-09-04", "2020-01-03"],  # hors plage de départ
        },
        fomc_dates=["2026-09-16", "2026-09-16"],  # doublon
        start=date(2022, 1, 1),
        end=date(2026, 12, 31),
    )
    assert records == [
        {"event_type": "CPI", "date": "2026-08-12"},
        {"event_type": "NFP", "date": "2026-09-04"},
        {"event_type": "CPI", "date": "2026-09-10"},
        {"event_type": "FOMC", "date": "2026-09-16"},
    ]


def test_build_records_sans_heures() -> None:
    # Les heures conventionnelles vivent dans calendar.py (MACRO.md §3) :
    # le fichier ne doit PAS les porter.
    records = build_records(fred_dates={}, fomc_dates=["2026-09-16"])
    assert records == [{"event_type": "FOMC", "date": "2026-09-16"}]
    assert "time_et" not in records[0]


def test_meme_jour_deux_types_conserves() -> None:
    records = build_records(
        fred_dates={"CPI": ["2026-09-10"], "PPI": ["2026-09-10"]},
        fomc_dates=[],
    )
    assert [r["event_type"] for r in records] == ["CPI", "PPI"]


def test_dates_fomc_curated_valides_et_convertibles() -> None:
    # Chaque date curated doit produire un événement FOMC valide à 14h00 ET.
    events = events_from_records(
        [{"event_type": "FOMC", "date": d} for d in FOMC_DECISION_DATES]
    )
    assert len(events) == len(set(FOMC_DECISION_DATES))
    assert all(e.event_type == "FOMC" for e in events)
    assert all(e.scheduled_at.hour in (18, 19) for e in events)  # 14h ET
