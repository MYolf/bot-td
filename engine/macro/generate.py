"""Génération du planning macro : FRED (CPI/NFP/PPI) + FOMC curated.

Outil HORS runtime (MACRO.md §4) : produit ``data/macro/events.json`` versionné
dans le repo. Le moteur ne fait AUCUN appel réseau — la génération est un
script à lancer manuellement, avec vérification trimestrielle du planning.

- Dates de release CPI/NFP/PPI : API FRED (clé gratuite ``FRED_API_KEY``).
  FRED donne la date de release OBSERVÉE (gère les reports type shutdown) ;
  l'heure est conventionnelle (8h30 ET), appliquée par ``calendar.py``.
- FOMC : dates de décision curated ci-dessous (le 2e jour de chaque réunion),
  l'annonce est à 14h00 ET.

Usage :

    python -m engine.macro.generate                       # plage par défaut
    python -m engine.macro.generate --start 2022-01-01 --end 2026-12-31
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date, timedelta
from pathlib import Path

import httpx

from engine.config import get_engine_settings

logger = logging.getLogger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred"
DEFAULT_OUT = Path("data/macro/events.json")
DEFAULT_START = date(2022, 1, 1)
DEFAULT_END_DAYS = 500  # ~16 mois : couvre le planning publié à l'avance

# Nom exact des releases FRED -> type macro v1.
RELEASE_NAMES: dict[str, str] = {
    "CPI": "Consumer Price Index",
    "NFP": "Employment Situation",
    "PPI": "Producer Price Index",
}

# Dates de décision FOMC (annonce 14h00 ET, 2e jour de réunion).
# Sources : calendriers publiés par la Fed. Les dates futures sont
# TENTATIVES (la Fed publie ~1 an à l'avance) — vérification trimestrielle
# obligatoire (MACRO.md §4).
FOMC_DECISION_DATES: tuple[str, ...] = (
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026 (tentatif)
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
)


async def resolve_release_id(client: httpx.AsyncClient, api_key: str, name: str) -> int:
    """Résout dynamiquement l'id FRED d'une release par son nom.

    Jamais d'id codé en dur : les ids ne sont pas garantis stables par FRED,
    la résolution par nom l'est.
    """
    response = await client.get(
        f"{FRED_BASE}/releases",
        params={"api_key": api_key, "file_type": "json"},
    )
    response.raise_for_status()
    releases = response.json().get("releases", [])
    exact = next((r for r in releases if r.get("name") == name), None)
    if exact is not None:
        return int(exact["id"])
    prefix = next((r for r in releases if str(r.get("name", "")).startswith(name)), None)
    if prefix is not None:
        logger.info(
            "FRED: release %r résolue par préfixe -> %r (id %s)",
            name, prefix.get("name"), prefix.get("id"),
        )
        return int(prefix["id"])
    raise LookupError(f"release FRED introuvable : {name!r}")


async def fetch_release_dates(
    client: httpx.AsyncClient, api_key: str, release_id: int
) -> list[str]:
    """Dates de release (passées et futures) d'une release FRED, ISO."""
    response = await client.get(
        f"{FRED_BASE}/release/dates",
        params={
            "api_key": api_key,
            "file_type": "json",
            "release_id": release_id,
            # inclus les dates futures déjà programmées (sans données)
            "include_release_dates_with_no_data": "true",
        },
    )
    response.raise_for_status()
    payload = response.json().get("release_dates", [])
    return [entry["date"] for entry in payload if "date" in entry]


def build_records(
    fred_dates: dict[str, list[str]],
    fomc_dates: list[str] = list(FOMC_DECISION_DATES),
    start: date | None = None,
    end: date | None = None,
) -> list[dict]:
    """Fusionne FRED + FOMC en enregistrements triés, filtrés, dédupliqués.

    Les heures conventionnelles sont volontairement OMISES : c'est
    ``calendar.py`` qui les applique (source unique de vérité, MACRO.md §3).
    """
    by_key: dict[tuple[str, str], dict] = {}
    for event_type, dates in {**fred_dates, "FOMC": fomc_dates}.items():
        for iso in dates:
            try:
                day = date.fromisoformat(iso)
            except ValueError:
                logger.warning("generate: date invalide ignorée %r", iso)
                continue
            if start is not None and day < start:
                continue
            if end is not None and day > end:
                continue
            by_key.setdefault((day, event_type), {"event_type": event_type, "date": iso})
    return [by_key[key] for key in sorted(by_key)]


async def generate(out: Path, start: date | None, end: date | None) -> list[dict]:
    api_key = get_engine_settings().fred_api_key
    if not api_key:
        raise SystemExit(
            "FRED_API_KEY absente du .env — clé gratuite sur "
            "https://fredaccount.stlouisfed.org/apikeys (études/génération "
            "uniquement, jamais requise au runtime du moteur)"
        )
    async with httpx.AsyncClient(timeout=30) as client:
        fred_dates: dict[str, list[str]] = {}
        for event_type, release_name in RELEASE_NAMES.items():
            release_id = await resolve_release_id(client, api_key, release_name)
            fred_dates[event_type] = await fetch_release_dates(client, api_key, release_id)
            logger.info("FRED %s (release %d) : %d dates", event_type, release_id, len(fred_dates[event_type]))
    records = build_records(fred_dates, start=start, end=end)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return records


async def _run(args: argparse.Namespace) -> None:
    end = date.fromisoformat(args.end) if args.end else date.today() + timedelta(days=DEFAULT_END_DAYS)
    start = date.fromisoformat(args.start) if args.start else DEFAULT_START
    records = await generate(Path(args.out), start, end)
    counts: dict[str, int] = {}
    for record in records:
        counts[record["event_type"]] = counts.get(record["event_type"], 0) + 1
    print(
        f"{args.out} : {len(records)} événements {start} -> {end} | "
        + " ".join(f"{t}={n}" for t, n in sorted(counts.items()))
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # httpx logue l'URL complète (query string incluse) -> la clé FRED ne doit
    # JAMAIS apparaître dans les logs (règle projet : secrets jamais loggés).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Génère le planning macro (FRED + FOMC).")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--start", default=None, help="YYYY-MM-DD (défaut 2022-01-01)")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD (défaut aujourd'hui + 500 j)")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
