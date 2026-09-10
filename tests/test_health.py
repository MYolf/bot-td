"""Tests des alertes de santé (app/services/health_monitor.py).

Catalogue : logique pure du silence moteur (heartbeat None / récent / ancien),
requête count_errors_since, cycle complet du service avec SQLite en mémoire
(période de grâce, alerte OK->KO, résolution KO->OK, anti-spam, échec d'envoi
retenté), heartbeat alimenté par le endpoint /internal/prices.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.database.models import Signal, Strategy
from app.database.repository import SignalRepository
from app.discord.embeds import GREEN, RED, build_health_alert_embed
from app.services import health_monitor
from app.services.health_monitor import (
    HealthAlertService,
    evaluate_engine_silence,
    record_price_update,
)

from tests.conftest import FakeNotifier

UTC = timezone.utc
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


class FakeBot:
    """Bot factice : seul is_ready() nous intéresse."""

    def __init__(self, ready: bool = True):
        self._ready = ready

    def is_ready(self) -> bool:
        return self._ready


class BrokenFactory:
    """Fabrique de sessions qui échoue toujours (base injoignable)."""

    def __call__(self):
        raise RuntimeError("base injoignable (test)")

    async def __aenter__(self):
        raise RuntimeError("base injoignable (test)")

    async def __aexit__(self, *args):
        return False


# --- Logique pure : silence du moteur ---

class TestEvaluateEngineSilence:
    def test_aucun_heartbeat_pas_d_alerte(self):
        # L'app peut démarrer avant le conteneur engine : pas d'alerte.
        check = evaluate_engine_silence(None, NOW, timedelta(minutes=30))
        assert check.ok is True
        assert check.component == "Moteur de signaux"

    def test_heartbeat_recent_ok(self):
        check = evaluate_engine_silence(
            NOW - timedelta(minutes=10), NOW, timedelta(minutes=30)
        )
        assert check.ok is True
        assert "10 min" in check.detail

    def test_silence_trop_long_ko(self):
        check = evaluate_engine_silence(
            NOW - timedelta(minutes=95), NOW, timedelta(minutes=30)
        )
        assert check.ok is False
        assert "95 min" in check.detail


class TestHealthEmbed:
    def test_alerte_rouge(self):
        embed = build_health_alert_embed(
            component="Base de données", detail="SELECT 1", resolved=False
        )
        assert embed.title == "🚨 Alerte santé — Base de données"
        assert embed.color.value == RED

    def test_resolution_verte(self):
        embed = build_health_alert_embed(
            component="Base de données", detail="SELECT 1", resolved=True
        )
        assert embed.title == "✅ Résolu — Base de données"
        assert embed.color.value == GREEN


# --- Requête count_errors_since ---

def _insert_signals(client, statuses: list[tuple[str, datetime]]) -> None:
    async def run() -> None:
        async with client.db_factory() as session:
            strategy = Strategy(name="momentum_v1")
            session.add(strategy)
            await session.flush()
            for index, (status, received_at) in enumerate(statuses):
                session.add(
                    Signal(
                        signal_uid=f"momentum_v1:BTCUSDT:15:{index}:{status}",
                        sequence_number=index + 1,
                        strategy_id=strategy.id,
                        symbol="BTCUSDT",
                        exchange="BINANCE",
                        timeframe="15",
                        action="BUY",
                        entry_price=Decimal("100"),
                        stop_loss=Decimal("98"),
                        take_profit=Decimal("104"),
                        risk_reward=Decimal("2"),
                        signal_timestamp=received_at,
                        received_at=received_at,
                        status=status,
                    )
                )
            await session.commit()

    asyncio.run(run())


class TestCountErrorsSince:
    def test_fenetre_glissante(self, client):
        _insert_signals(
            client,
            [
                ("ERROR", NOW - timedelta(minutes=10)),
                ("ERROR", NOW - timedelta(minutes=30)),
                ("ERROR", NOW - timedelta(hours=3)),  # hors fenêtre
                ("SENT", NOW - timedelta(minutes=5)),  # pas ERROR
            ],
        )

        async def run() -> int:
            async with client.db_factory() as session:
                return await SignalRepository(session).count_errors_since(
                    NOW - timedelta(hours=1)
                )

        assert asyncio.run(run()) == 2


# --- Service complet (SQLite en mémoire) ---

def _service(client, notifier: FakeNotifier, bot: FakeBot, **kwargs) -> HealthAlertService:
    return HealthAlertService(client.db_factory, notifier, bot, **kwargs)


@pytest.fixture(autouse=True)
def _reset_heartbeat(monkeypatch):
    """Isoler le heartbeat module-level entre les tests."""
    monkeypatch.setattr(health_monitor, "_last_price_update", None)


class TestHealthAlertService:
    def test_premier_cycle_sans_alerte(self, client):
        # Grâce du premier cycle : le bot peut être encore en cours de connexion.
        notifier = FakeNotifier()
        service = _service(client, notifier, FakeBot(ready=False))
        changed = asyncio.run(service.check_once(NOW))
        assert changed == []
        assert notifier.sent == []

    def test_alerte_puis_resolution_bot(self, client):
        notifier = FakeNotifier()
        bot = FakeBot(ready=True)
        service = _service(client, notifier, bot)
        asyncio.run(service.check_once(NOW))  # grâce : états initiaux

        bot._ready = False
        changed = asyncio.run(service.check_once(NOW))
        assert [c.component for c in changed] == ["Bot Discord"]
        assert notifier.sent[0].title == "🚨 Alerte santé — Bot Discord"

        # Toujours KO : anti-spam, aucun nouvel envoi.
        assert asyncio.run(service.check_once(NOW)) == []
        assert len(notifier.sent) == 1

        bot._ready = True
        changed = asyncio.run(service.check_once(NOW))
        assert [c.component for c in changed] == ["Bot Discord"]
        assert notifier.sent[1].title == "✅ Résolu — Bot Discord"

    def test_alerte_base_injoignable(self, client):
        notifier = FakeNotifier()
        service = HealthAlertService(
            BrokenFactory(), notifier, FakeBot(ready=True)
        )
        # Une panne présente dès le premier cycle est alertée immédiatement.
        changed = asyncio.run(service.check_once(NOW))
        # Base KO : le contrôle des signaux ERROR est omis, pas d'alerte fantôme.
        assert [c.component for c in changed] == ["Base de données"]
        assert notifier.sent[0].title == "🚨 Alerte santé — Base de données"

    def test_alerte_moteur_silencieux_puis_resolution(self, client):
        notifier = FakeNotifier()
        record_price_update(NOW - timedelta(minutes=45))
        service = _service(
            client, notifier, FakeBot(ready=True),
            engine_max_silence_seconds=1800,
        )
        changed = asyncio.run(service.check_once(NOW))
        assert [c.component for c in changed] == ["Moteur de signaux"]
        assert notifier.sent[0].title == "🚨 Alerte santé — Moteur de signaux"
        # Le moteur reprend : résolution uniquement.
        record_price_update(NOW - timedelta(minutes=1))
        changed = asyncio.run(service.check_once(NOW))
        assert [c.component for c in changed] == ["Moteur de signaux"]
        assert notifier.sent[1].title == "✅ Résolu — Moteur de signaux"

    def test_alerte_moteur_silencieux_apres_activation(self, client):
        # Démarrage nominal puis panne : alerte OK -> KO.
        notifier = FakeNotifier()
        service = _service(client, notifier, FakeBot(ready=True))
        record_price_update(NOW - timedelta(minutes=1))
        asyncio.run(service.check_once(NOW))
        record_price_update(NOW - timedelta(minutes=120))
        changed = asyncio.run(service.check_once(NOW + timedelta(minutes=120)))
        assert [c.component for c in changed] == ["Moteur de signaux"]
        assert notifier.sent[0].title == "🚨 Alerte santé — Moteur de signaux"
        # Âge = (NOW + 120 min) - (NOW - 120 min) = 240 min.
        assert "240 min" in notifier.sent[0].description

    def test_alerte_cascade_erreurs(self, client):
        _insert_signals(
            client,
            [
                ("ERROR", NOW - timedelta(minutes=10)),
                ("ERROR", NOW - timedelta(minutes=20)),
                ("ERROR", NOW - timedelta(minutes=40)),
            ],
        )
        notifier = FakeNotifier()
        service = _service(client, notifier, FakeBot(ready=True))
        changed = asyncio.run(service.check_once(NOW))
        assert [c.component for c in changed] == ["Signaux en erreur"]
        assert notifier.sent[0].title == "🚨 Alerte santé — Signaux en erreur"
        # Retour sous le seuil (erreurs purgées) -> résolution.
        async def purge_errors() -> None:
            async with client.db_factory() as session:
                for signal in await session.scalars(
                    select(Signal).where(Signal.status == "ERROR")
                ):
                    await session.delete(signal)
                await session.commit()

        asyncio.run(purge_errors())
        changed = asyncio.run(service.check_once(NOW))
        assert [c.component for c in changed] == ["Signaux en erreur"]
        assert notifier.sent[1].title == "✅ Résolu — Signaux en erreur"

    def test_echec_envoi_transition_retentee(self, client):
        # L'envoi échoue : la transition n'est pas consommée, elle est
        # retentée au cycle suivant.
        notifier = FakeNotifier()
        bot = FakeBot(ready=True)
        service = _service(client, notifier, bot)
        asyncio.run(service.check_once(NOW))  # grâce : Bot Discord OK

        notifier.fail = True
        bot._ready = False
        assert asyncio.run(service.check_once(NOW)) == []
        assert notifier.sent == []

        notifier.fail = False
        changed = asyncio.run(service.check_once(NOW))
        assert [c.component for c in changed] == ["Bot Discord"]
        assert notifier.sent[0].title == "🚨 Alerte santé — Bot Discord"


# --- Heartbeat alimenté par le endpoint interne ---

class TestHeartbeatEndpoint:
    def test_price_update_horodate_le_heartbeat(self, client):
        assert health_monitor.last_price_update() is None
        response = client.post(
            "/internal/prices",
            json={
                "secret": "secret-test",
                "symbol": "BTCUSDT",
                "timeframe": "15",
                "open_time": 1756800000000,
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
            },
        )
        assert response.status_code == 200
        assert health_monitor.last_price_update() is not None

    def test_secret_invalide_pas_de_heartbeat(self, client):
        response = client.post(
            "/internal/prices",
            json={
                "secret": "mauvais-secret",
                "symbol": "BTCUSDT",
                "timeframe": "15",
                "open_time": 1756800000000,
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
            },
        )
        assert response.status_code == 401
        assert health_monitor.last_price_update() is None
