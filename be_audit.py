"""Audit rétrospectif de la règle break-even (+1,5R) sur les 18 trades réels.

Rejoue chaque trade paper de production sur les bougies 15m Binance avec les
règles ACTUELLES de app/paper_trading/engine.py (commit f734269), fidèle à
l'ordre de /internal/prices :

1. check_candle : stop actif = entrée si be_notified, sinon SL d'origine ;
   SL prioritaire sur TP si la bougie touche les deux (resolve_exit_candle) ;
   un SL touché alors que be_notified est déjà vrai devient "BE" (0R).
2. check_break_even : APRÈS les clôtures — le drapeau est posé sur une bougie
   qui n'a PAS clôturé la position, donc le BE ne s'applique jamais à sa
   bougie déclencheuse (anti-lookahead).

Le replay est borné à la durée de vie réelle du trade (opened_at ->
closed_at) : la question est « la règle BE aurait-elle changé CE résultat »,
pas « que serait-il arrivé après la clôture réelle ».

Prix en Decimal (comparaisons strictes identiques à la production, les
klines Binance sont des chaînes exactes).

Usage : python be_audit.py   (lecture seule, aucun impact production)
"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

BASE_URL = "https://api.binance.com"
INTERVAL = "15m"
BE_TRIGGER_R = Decimal("1.5")

# Les 18 trades réels, exportés de la base de prod le 2026-09-16 (UTC).
# (n, symbole, action, entrée, SL, TP, opened_at, closed_at, raison réelle, R réel)
TRADES = [
    (1, "BTCUSDT", "BUY", "78647.20", "77860.728", "80220.144",
     "2026-08-31T16:30:27", "2026-09-01T19:45:15", "SL", "-1"),
    (2, "ETHUSDT", "BUY", "2473.47", "2448.7353", "2522.9394",
     "2026-08-31T18:15:20", "2026-09-01T12:45:43", "SL", "-1"),
    (3, "BTCUSDT", "SELL", "77881.88", "78660.6988", "76324.2424",
     "2026-09-01T10:15:15", "2026-09-02T11:00:06", "TP", "2"),
    (4, "ETHUSDT", "SELL", "2446.22", "2470.6822", "2397.2956",
     "2026-09-01T12:45:43", "2026-09-02T01:45:08", "TP", "2"),
    (5, "ETHUSDT", "SELL", "2395.15", "2419.1015", "2347.247",
     "2026-09-02T01:45:09", "2026-09-02T03:15:36", "SL", "-1"),
    (6, "ETHUSDT", "SELL", "2396.23", "2420.1923", "2348.3054",
     "2026-09-02T08:45:28", "2026-09-03T12:45:43", "SL", "-1"),
    (7, "ETHUSDT", "BUY", "2489.46", "2464.5654", "2539.2492",
     "2026-09-03T15:00:15", "2026-09-04T09:30:02", "TP", "2"),
    (8, "BTCUSDT", "BUY", "81267.64", "80454.9636", "82892.9928",
     "2026-09-04T04:30:31", "2026-09-04T12:45:21", "SL", "-1"),
    (9, "ETHUSDT", "BUY", "2481.01", "2456.1999", "2530.6302",
     "2026-09-05T18:00:41", "2026-09-07T02:45:32", "TP", "2"),
    (10, "ETHUSDT", "BUY", "2512.11", "2486.9889", "2562.3522",
     "2026-09-09T04:45:09", "2026-09-09T11:00:42", "SL", "-1"),
    (11, "ETHUSDT", "SELL", "2461.80", "2486.418", "2412.564",
     "2026-09-09T20:30:03", "2026-09-10T13:00:25", "TP", "2"),
    (12, "ETHUSDT", "BUY", "2524.23", "2498.9877", "2574.7146",
     "2026-09-11T13:45:03", "2026-09-11T14:00:16", "TP", "2"),
    (13, "ETHUSDT", "BUY", "2537.06", "2511.6894", "2587.8012",
     "2026-09-12T11:45:03", "2026-09-13T07:00:44", "SL", "-1"),
    (14, "BTCUSDT", "SELL", "76934.00", "77703.34", "75395.32",
     "2026-09-13T22:15:21", "2026-09-14T03:00:30", "SL", "-1"),
    (15, "ETHUSDT", "SELL", "2488.18", "2513.0618", "2438.4164",
     "2026-09-13T22:15:22", "2026-09-14T03:00:30", "SL", "-1"),
    (16, "BTCUSDT", "BUY", "78141.57", "77360.1543", "79704.4014",
     "2026-09-14T10:00:16", "2026-09-15T05:15:01", "SL", "-1"),
    (17, "ETHUSDT", "SELL", "2478.35", "2503.1335", "2428.783",
     "2026-09-15T07:45:03", "2026-09-15T14:45:05", "TP", "2"),
    (18, "BTCUSDT", "SELL", "76856.01", "77624.5701", "75318.8898",
     "2026-09-15T08:30:25", "2026-09-15T19:00:26", "TP", "2"),
]


@dataclass(frozen=True)
class ReplayResult:
    n: int
    be_armed: bool
    armed_at: datetime | None  # bougie qui a armé le BE (+1,5R touché)
    exit_reason: str  # TP / SL / BE / OUVERT
    exit_at: datetime | None
    result_r: Decimal  # +2 / -1 / 0


def floor_15m(dt: datetime) -> datetime:
    """Borne de clôture de la bougie signal : opened_at est quelques secondes
    APRÈS cette borne, donc le floor 15 min redonne le timestamp du signal."""
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


async def fetch_candles(
    client: httpx.AsyncClient, symbol: str, start: datetime, end: datetime
) -> list[tuple[datetime, Decimal, Decimal]]:
    """Bougies 15m [start, end] : (open_time, high, low) en Decimal exacts."""
    bougies: list[tuple[datetime, Decimal, Decimal]] = []
    cursor = start
    while cursor < end:
        reps = await client.get(
            f"{BASE_URL}/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": INTERVAL,
                "startTime": int(cursor.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
                "limit": 1000,
            },
        )
        reps.raise_for_status()
        lignes = reps.json()
        if not lignes:
            break
        for k in lignes:
            bougies.append(
                (
                    datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                    Decimal(k[2]),  # high
                    Decimal(k[3]),  # low
                )
            )
        cursor = bougies[-1][0] + timedelta(minutes=15)
        if len(lignes) < 1000:
            break
    return bougies


def replay(
    n: int,
    action: str,
    entry: Decimal,
    stop_loss: Decimal,
    take_profit: Decimal,
    bougies: list[tuple[datetime, Decimal, Decimal]],
) -> ReplayResult:
    risk = entry - stop_loss if action == "BUY" else stop_loss - entry
    sign = Decimal("1") if action == "BUY" else Decimal("-1")
    be_level = entry + sign * BE_TRIGGER_R * risk

    be_armed = False
    armed_at: datetime | None = None

    for open_time, high, low in bougies:
        # --- check_candle : stop actif = entrée si BE armé, SL prioritaire ---
        stop_actif = entry if be_armed else stop_loss
        exit_reason: str | None = None
        if action == "BUY":
            if low <= stop_actif:
                exit_reason = "BE" if be_armed else "SL"
            elif high >= take_profit:
                exit_reason = "TP"
        else:  # SELL
            if high >= stop_actif:
                exit_reason = "BE" if be_armed else "SL"
            elif low <= take_profit:
                exit_reason = "TP"
        if exit_reason is not None:
            return ReplayResult(
                n=n,
                be_armed=be_armed,
                armed_at=armed_at,
                exit_reason=exit_reason,
                exit_at=open_time,
                result_r={"TP": Decimal("2"), "SL": Decimal("-1"), "BE": Decimal("0")}[
                    exit_reason
                ],
            )

        # --- check_break_even : APRÈS les clôtures (anti-lookahead : le BE
        # ne peut pas s'appliquer à la bougie qui l'a déclenché) ---
        if not be_armed:
            touche = high >= be_level if action == "BUY" else low <= be_level
            if touche:
                be_armed = True
                armed_at = open_time

    return ReplayResult(n, be_armed, armed_at, "OUVERT", None, Decimal("0"))


def fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%d/%m %H:%M") if dt else "—"


async def main() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        print(
            "Audit BE (+1,5R armé -> retour entrée = 0R) — replay règles "
            "production sur bougies 15m Binance\n"
        )
        lignes: list[tuple] = []
        total_reel = Decimal("0")
        total_replay = Decimal("0")

        for t in TRADES:
            (n, symbol, action, e, sl, tp, ouv, ferm, raison, r_reel) = t
            opened = datetime.fromisoformat(ouv).replace(tzinfo=timezone.utc)
            closed = datetime.fromisoformat(ferm).replace(tzinfo=timezone.utc)
            # Première bougie vérifiée : celle qui OUVRE à la borne de clôture
            # de la bougie signal (anti-lookahead, cf. check_candle).
            bougies = await fetch_candles(
                client, symbol, floor_15m(opened), closed + timedelta(minutes=5)
            )
            bougies = [b for b in bougies if b[0] < closed + timedelta(minutes=20)]
            res = replay(n, action, Decimal(e), Decimal(sl), Decimal(tp), bougies)

            reel = Decimal(r_reel)
            total_reel += reel
            total_replay += res.result_r
            delta = res.result_r - reel

            verdict = {
                ("SL", "BE"): "BE SAUVÉ (+1R)",
                ("TP", "BE"): "BE COÛTÉ (−2R)",
            }.get((raison, res.exit_reason), "identique" if raison == res.exit_reason else "≠")

            lignes.append(
                (n, symbol, action, raison, reel, res, delta, verdict)
            )
            await asyncio.sleep(0.3)  # politesse rate limit

        # --- Tableau détaillé ---
        entete = (
            f"{'#':>2} {'symbole':<8} {'sens':<4} | "
            f"{'réel':<9} {'R':>4} | {'BE armé':<11} {'armé le':<11} | "
            f"{'replay':<9} {'R':>4} | {'verdict':<16}"
        )
        print(entete)
        print("-" * len(entete))
        for n, symbol, action, raison, reel, res, delta, verdict in lignes:
            be = "oui" if res.be_armed else "non"
            print(
                f"{n:>2} {symbol:<8} {action:<4} | "
                f"{raison:<9} {reel:>4} | {be:<11} {fmt_dt(res.armed_at):<11} | "
                f"{res.exit_reason:<9} {res.result_r:>4} | {verdict:<16}"
            )

        # --- Synthèse ---
        print("\nSynthèse — règle BE appliquée rétroactivement aux 18 trades :")
        sauves = [l for l in lignes if l[7].startswith("BE SAUVÉ")]
        coutes = [l for l in lignes if l[7].startswith("BE COÛTÉ")]
        differents = [l for l in lignes if l[7] not in ("identique",)]
        for label, lot in (("BE aurait sauvé un SL (−1R -> 0R)", sauves),
                           ("BE aurait coûté un TP (+2R -> 0R)", coutes)):
            ids = ", ".join(f"#{l[0]}" for l in lot) or "—"
            print(f"  {label} : {len(lot)}  [{ids}]")
        autres = [l for l in differents if l not in sauves and l not in coutes]
        if autres:
            print(f"  Autres écarts replay/réel : {len(autres)}  "
                  f"[{', '.join(f'#{l[0]} ({l[3]}->{l[5].exit_reason})' for l in autres)}]")
        sl_sans_be = [l for l in lignes if l[3] == "SL" and not l[5].be_armed]
        print(f"  SL sans jamais armer le BE (+1,5R jamais touché) : "
              f"{len(sl_sans_be)}  [{', '.join(f'#{l[0]}' for l in sl_sans_be) or '—'}]")
        print(f"\n  Réel : {total_reel:+} R   Replay avec règle BE : {total_replay:+} R   "
              f"Delta : {total_replay - total_reel:+} R")


if __name__ == "__main__":
    asyncio.run(main())
