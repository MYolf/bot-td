"""Tests du récap hebdomadaire (app/services/weekly_recap.py).

Catalogue : calcul de la prochaine échéance (vendredi 22h Paris, heure
d'été/hiver), requêtes opened_between / closed_between / open_all, embed du
récap (sections, bilan R), envoi via le service avec notifieur factice.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.database.models import PaperPosition, PaperTrade, Signal, Strategy
from app.database.repository import PaperRepository
from app.discord.embeds import build_weekly_recap_embed
from app.services.weekly_recap import WeeklyRecapService, compute_next_run

from tests.conftest import FakeNotifier

PARIS = ZoneInfo("Europe/Paris")
UTC = timezone.utc
VENDREDI = 4

# Fin août : Paris est à l'heure d'été (UTC+2). Le 31/08/2026 est un lundi.
NOW = datetime(2026, 8, 31, 18, 0, tzinfo=UTC)  # lundi 20:00 heure de Paris
SEMAINE_DEBUT_UTC = NOW - timedelta(days=7)


# --- Logique pure : échéance ---

class TestComputeNextRun:
    def test_depuis_un_lundi_prochain_vendredi(self):
        now = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)  # lundi 12:00 Paris
        cible = compute_next_run(now, 22, VENDREDI, PARIS)
        # Vendredi 04/09/2026 à 22:00 Paris = 20:00 UTC.
        assert cible == datetime(2026, 9, 4, 20, 0, tzinfo=UTC)

    def test_vendredi_avant_22h_meme_jour(self):
        now = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)  # vendredi 12:00 Paris
        cible = compute_next_run(now, 22, VENDREDI, PARIS)
        assert cible == datetime(2026, 9, 4, 20, 0, tzinfo=UTC)

    def test_vendredi_apres_22h_vendredi_suivant(self):
        now = datetime(2026, 9, 4, 21, 0, tzinfo=UTC)  # vendredi 23:00 Paris
        cible = compute_next_run(now, 22, VENDREDI, PARIS)
        assert cible == datetime(2026, 9, 11, 20, 0, tzinfo=UTC)

    def test_vendredi_exactement_22h_vendredi_suivant(self):
        now = datetime(2026, 9, 4, 20, 0, tzinfo=UTC)  # vendredi 22:00 Paris pile
        cible = compute_next_run(now, 22, VENDREDI, PARIS)
        assert cible == datetime(2026, 9, 11, 20, 0, tzinfo=UTC)

    def test_dimanche_vendredi_suivant(self):
        now = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)  # dimanche
        cible = compute_next_run(now, 22, VENDREDI, PARIS)
        assert cible == datetime(2026, 9, 11, 20, 0, tzinfo=UTC)

    def test_heure_d_hiver_utc_moins_1(self):
        # 29/01/2027 est un vendredi ; Paris à UTC+1 -> 22:00 Paris = 21:00 UTC.
        now = datetime(2027, 1, 29, 10, 0, tzinfo=UTC)
        cible = compute_next_run(now, 22, VENDREDI, PARIS)
        assert cible == datetime(2027, 1, 29, 21, 0, tzinfo=UTC)


# --- Construction des données de test ---

def _seed(client) -> None:
    """Quatre positions : ouverte cette semaine, clôturée cette semaine (TP),
    clôturée la semaine PRÉCÉDENTE (SL, pour la comparaison du bilan),
    ouverte avant la semaine écoulée (toujours en cours)."""

    async def run() -> None:
        async with client.db_factory() as session:
            strategy = Strategy(name="momentum_v1")
            session.add(strategy)
            await session.flush()

            compteur = 0

            def signal(symbol: str, action: str, ts: datetime) -> Signal:
                nonlocal compteur
                compteur += 1
                return Signal(
                    signal_uid=f"momentum_v1:{symbol}:15:{ts.isoformat()}:{action}",
                    sequence_number=compteur,
                    strategy_id=strategy.id,
                    symbol=symbol,
                    exchange="BINANCE",
                    timeframe="15",
                    action=action,
                    entry_price=Decimal("100"),
                    stop_loss=Decimal("98"),
                    take_profit=Decimal("104"),
                    risk_reward=Decimal("2"),
                    signal_timestamp=ts,
                    received_at=ts,
                    status="SENT",
                )

            hors_semaine = SEMAINE_DEBUT_UTC - timedelta(days=2)
            semaine_precedente = SEMAINE_DEBUT_UTC - timedelta(days=1)
            # 1) Position BTC ouverte cette semaine — en cours.
            s_btc = signal("BTCUSDT", "BUY", datetime(2026, 8, 30, 10, 0, tzinfo=UTC))
            session.add(s_btc)
            await session.flush()
            session.add(
                PaperPosition(
                    signal_id=s_btc.id,
                    status="OPEN",
                    opened_at=datetime(2026, 8, 30, 10, 0, tzinfo=UTC),
                )
            )
            # 2) Position ETH ouverte hors semaine, clôturée cette semaine en TP.
            s_eth = signal("ETHUSDT", "SELL", hors_semaine)
            session.add(s_eth)
            await session.flush()
            position_eth = PaperPosition(
                signal_id=s_eth.id,
                status="CLOSED",
                opened_at=hors_semaine,
                closed_at=datetime(2026, 8, 28, 14, 0, tzinfo=UTC),
                result_r=Decimal("2"),
            )
            session.add(position_eth)
            await session.flush()
            session.add(
                PaperTrade(
                    paper_position_id=position_eth.id,
                    exit_reason="TP",
                    exit_price=Decimal("104"),
                    closed_at=datetime(2026, 8, 28, 14, 0, tzinfo=UTC),
                )
            )
            # 3) Position XRP clôturée la semaine précédente en SL (-1 R).
            s_xrp = signal("XRPUSDT", "BUY", semaine_precedente - timedelta(days=1))
            session.add(s_xrp)
            await session.flush()
            position_xrp = PaperPosition(
                signal_id=s_xrp.id,
                status="CLOSED",
                opened_at=semaine_precedente - timedelta(days=1),
                closed_at=semaine_precedente,
                result_r=Decimal("-1"),
            )
            session.add(position_xrp)
            await session.flush()
            session.add(
                PaperTrade(
                    paper_position_id=position_xrp.id,
                    exit_reason="SL",
                    exit_price=Decimal("98"),
                    closed_at=semaine_precedente,
                )
            )
            # 4) Position SOL ouverte hors semaine — toujours en cours.
            s_sol = signal("SOLUSDT", "BUY", hors_semaine)
            session.add(s_sol)
            await session.flush()
            session.add(PaperPosition(signal_id=s_sol.id, status="OPEN", opened_at=hors_semaine))
            await session.commit()

    asyncio.run(run())


def _partition(client):
    async def run():
        async with client.db_factory() as session:
            repository = PaperRepository(session)
            return (
                await repository.opened_between(SEMAINE_DEBUT_UTC, NOW),
                await repository.closed_between(SEMAINE_DEBUT_UTC, NOW),
                await repository.open_all(),
            )

    return asyncio.run(run())


def _partition_precedente(client):
    async def run():
        async with client.db_factory() as session:
            repository = PaperRepository(session)
            return (
                None,
                await repository.closed_between(
                    SEMAINE_DEBUT_UTC - timedelta(days=7), SEMAINE_DEBUT_UTC
                ),
                None,
            )

    return asyncio.run(run())


# --- Requêtes du récap ---

class TestRequetesRecap:
    def test_partition_de_la_semaine(self, client):
        _seed(client)
        ouvertes, cloturees, en_cours = _partition(client)

        assert [signal.symbol for _p, signal, _s in ouvertes] == ["BTCUSDT"]
        assert [signal.symbol for _p, _t, signal, _s in cloturees] == ["ETHUSDT"]
        # Ordre chronologique d'ouverture : SOL (hors semaine) puis BTC.
        assert [signal.symbol for _p, signal, _s in en_cours] == ["SOLUSDT", "BTCUSDT"]

    def test_aucune_donnee(self, client):
        assert _partition(client) == ([], [], [])


# --- Embed du récap ---

class TestEmbedRecap:
    def test_sections_et_bilan(self, client):
        _seed(client)
        ouvertes, cloturees, en_cours = _partition(client)
        _, cloturees_precedentes, _ = _partition_precedente(client)

        embed = build_weekly_recap_embed(
            debut=SEMAINE_DEBUT_UTC.astimezone(PARIS),
            fin=NOW.astimezone(PARIS),
            ouvertes_semaine=ouvertes,
            cloturees_semaine=cloturees,
            cloturees_semaine_precedente=cloturees_precedentes,
            en_cours=en_cours,
        )
        texte = {f.name: f.value for f in embed.fields}

        assert "BTCUSDT" in texte["📈 Nouvelles positions (1)"]
        assert "LONG" in texte["📈 Nouvelles positions (1)"]
        # Jour + heure d'ouverture (Paris = UTC+2 fin août).
        assert "ouvert le dimanche 30/08 12:00" in texte["📈 Nouvelles positions (1)"]
        assert "ETHUSDT" in texte["🏁 Clôturées cette semaine (1)"]
        assert "TP @ 104" in texte["🏁 Clôturées cette semaine (1)"]
        assert "+2 R" in texte["🏁 Clôturées cette semaine (1)"]
        # Ouverture -> clôture avec jour et heure (Paris) : ETH ouverte le
        # 22/08 18:00 UTC, clôturée le 28/08 14:00 UTC.
        assert (
            "samedi 22/08 20:00 → vendredi 28/08 16:00"
            in texte["🏁 Clôturées cette semaine (1)"]
        )
        # Les deux positions ouvertes (y compris l'ancienne) restent affichées,
        # avec le jour et l'heure locale (plus d'UTC affiché nu).
        assert "BTCUSDT" in texte["⏳ En cours (2)"]
        assert "SOLUSDT" in texte["⏳ En cours (2)"]
        assert "depuis le samedi 22/08 20:00" in texte["⏳ En cours (2)"]
        assert "UTC" not in texte["⏳ En cours (2)"]

        # Numéro de trade sur chaque ligne (ordre de création : BTC #1, ETH #2).
        assert "#1 **BTCUSDT**" in texte["📈 Nouvelles positions (1)"]
        assert "#2 **ETHUSDT**" in texte["🏁 Clôturées cette semaine (1)"]
        assert "#4 **SOLUSDT**" in texte["⏳ En cours (2)"]

        # Bilan enrichi : 1 trade TP (+2 R), PF indéfini (aucune perte).
        bilan = texte["Résultat de la semaine"]
        assert "1 trades · win 100.00% · +2 R" in bilan
        assert "PF —" in bilan
        assert "Meilleur +2 R · Pire +2 R" in bilan
        # Comparaison S-1 : -1 R la semaine précédente -> delta +3 R.
        assert "Semaine précédente : -1 R (1 trades)" in bilan
        assert "Δ +3 R" in bilan

        # Ventilation par direction : seule la clôture SELL de la semaine compte.
        assert texte["Par direction (R)"] == "🔴 SELL : 1 trades · 100.00% · 2.00 R"
        assert "semaine du 24/08/2026 au 31/08/2026" in embed.title

    def test_semaine_sans_activite(self):
        embed = build_weekly_recap_embed(
            debut=SEMAINE_DEBUT_UTC.astimezone(PARIS),
            fin=NOW.astimezone(PARIS),
            ouvertes_semaine=[],
            cloturees_semaine=[],
            en_cours=[],
        )
        texte = {f.name: f.value for f in embed.fields}
        assert texte["📈 Nouvelles positions (0)"] == "—"
        assert texte["🏁 Clôturées cette semaine (0)"] == "—"
        assert texte["⏳ En cours (0)"] == "—"
        assert texte["Résultat de la semaine"] == "Aucune clôture cette semaine"
        assert "Par direction (R)" not in texte


# --- Service complet (base + notifieur factice) ---

class TestWeeklyRecapService:
    def test_post_recap_publie_l_embed(self, client):
        _seed(client)
        fake = FakeNotifier()
        service = WeeklyRecapService(client.db_factory, fake)

        message_id = asyncio.run(service.post_recap(now_utc=NOW))

        assert message_id == 1001
        (embed,) = fake.sent
        assert "📅 Récap hebdomadaire — semaine du 24/08/2026 au 31/08/2026" in embed.title
        texte = {f.name: f.value for f in embed.fields}
        assert "BTCUSDT" in texte["📈 Nouvelles positions (1)"]
        assert "+2 R" in texte["Résultat de la semaine"]
        # La comparaison S-1 est alimentée par le service (clôtures S-1 : -1 R).
        assert "Semaine précédente : -1 R (1 trades)" in texte["Résultat de la semaine"]
        assert "Par direction (R)" in texte

    def test_run_absorbe_les_echecs(self, client, monkeypatch):
        """Un échec (Discord KO, base KO) est loggé et absorbé : la boucle
        continue vers le récap suivant (vérifié par l'appel n°2)."""
        import app.services.weekly_recap as weekly_recap_module

        # Échéance immédiate pour ne pas attendre vendredi dans le test.
        monkeypatch.setattr(
            weekly_recap_module,
            "compute_next_run",
            lambda now, hour, weekday, tz: now,  # remaining <= 0 -> envoi immédiat
        )
        service = WeeklyRecapService(client.db_factory, FakeNotifier())
        appels = 0

        async def post_recap(now_utc=None):
            nonlocal appels
            appels += 1
            if appels == 1:
                raise RuntimeError("Discord indisponible (test)")
            raise asyncio.CancelledError()  # stoppe la boucle proprement

        service.post_recap = post_recap

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(service.run())

        assert appels == 2  # le premier échec n'a pas arrêté la boucle
