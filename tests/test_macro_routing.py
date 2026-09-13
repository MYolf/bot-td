"""Tests du routage macro display-only (MACRO.md §10).

Un signal annoté HIGH/EXTREME est publié dans le salon macro dédié (s'il est
configuré) au lieu du salon des signaux. Aucun blocage, aucun changement du
pipeline : c'est du routage de visibilité uniquement.
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.database.models import Signal
from tests.conftest import FakeNotifier


def _payload(**overrides) -> dict:
    payload = {
        "secret": "secret-test",
        "strategy": "momentum_v1",
        "symbol": "BTCUSDT",
        "exchange": "BINANCE",
        "timeframe": "15",
        "action": "BUY",
        "price": "104532.42",
        "stop_loss": "103800.00",
        "take_profit": "106000.00",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(overrides)
    return payload


def _avec_macro_notifier() -> FakeNotifier:
    from app.services.discord_service import set_macro_notifier

    fake_macro = FakeNotifier()
    set_macro_notifier(fake_macro)
    return fake_macro


def _nettoie() -> None:
    from app.services.discord_service import set_macro_notifier

    set_macro_notifier(None)


def _signals_en_base(client) -> list[Signal]:
    async def load():
        async with client.db_factory() as session:
            return list((await session.execute(select(Signal))).scalars().all())

    return asyncio.run(load())


class TestRoutageMacro:
    def test_signal_high_route_vers_le_salon_macro(self, client):
        fake_macro = _avec_macro_notifier()
        try:
            response = client.post(
                "/webhook/tradingview",
                json=_payload(macro_level="HIGH", macro_note="FOMC dans 10 min"),
            )
        finally:
            _nettoie()

        assert response.status_code == 200
        assert response.json()["status"] == "sent"
        # Salon macro : 1 embed ; salon des signaux : rien.
        assert len(fake_macro.sent) == 1
        assert len(client.notifier.sent) == 0

    def test_signal_extreme_route_vers_le_salon_macro(self, client):
        fake_macro = _avec_macro_notifier()
        try:
            response = client.post(
                "/webhook/tradingview",
                json=_payload(macro_level="EXTREME", macro_note="CPI dans 5 min"),
            )
        finally:
            _nettoie()

        assert response.json()["status"] == "sent"
        assert len(fake_macro.sent) == 1
        assert len(client.notifier.sent) == 0

    def test_embed_macro_contient_le_champ_dedie(self, client):
        fake_macro = _avec_macro_notifier()
        try:
            client.post(
                "/webhook/tradingview",
                json=_payload(macro_level="EXTREME", macro_note="FOMC dans 5 min"),
            )
        finally:
            _nettoie()

        embed = fake_macro.sent[0]
        champs = {f.name: f.value for f in embed.fields}
        assert "Macro" in champs
        assert "FOMC dans 5 min" in champs["Macro"]
        assert "EXTREME" in champs["Macro"]

    def test_sans_salon_macro_configure_reste_dans_le_salon_signaux(self, client):
        _nettoie()  # notifieur macro None (défaut après chaque test, explicite ici)
        response = client.post(
            "/webhook/tradingview",
            json=_payload(macro_level="HIGH", macro_note="NFP dans 20 min"),
        )

        assert response.json()["status"] == "sent"
        assert len(client.notifier.sent) == 1
        embed = client.notifier.sent[0]
        champs = {f.name: f.value for f in embed.fields}
        assert "Macro" in champs  # l'annotation reste visible

    def test_signal_sans_contexte_macro_reste_dans_le_salon_signaux(self, client):
        fake_macro = _avec_macro_notifier()
        try:
            response = client.post("/webhook/tradingview", json=_payload())
        finally:
            _nettoie()

        assert response.json()["status"] == "sent"
        assert len(client.notifier.sent) == 1
        assert len(fake_macro.sent) == 0
        champs = {f.name: f.value for f in client.notifier.sent[0].fields}
        assert "Macro" not in champs

    def test_signal_low_reste_dans_le_salon_signaux(self, client):
        """Seuls HIGH/EXTREME sont routés ; LOW (ou absent) = salon normal."""
        fake_macro = _avec_macro_notifier()
        try:
            response = client.post(
                "/webhook/tradingview",
                json=_payload(macro_level="LOW"),
            )
        finally:
            _nettoie()

        assert response.json()["status"] == "sent"
        assert len(client.notifier.sent) == 1
        assert len(fake_macro.sent) == 0
        champs = {f.name: f.value for f in client.notifier.sent[0].fields}
        assert "Macro" not in champs

    def test_contexte_macro_persiste_en_base(self, client):
        client.post(
            "/webhook/tradingview",
            json=_payload(macro_level="EXTREME", macro_note="CPI dans 5 min"),
        )
        (signal,) = _signals_en_base(client)
        assert signal.macro_level == "EXTREME"
        assert signal.macro_note == "CPI dans 5 min"

    def test_macro_level_invalide_rejete_en_422(self, client):
        response = client.post(
            "/webhook/tradingview",
            json=_payload(macro_level="APOCALYPSE"),
        )
        assert response.status_code == 422
        assert _signals_en_base(client) == []

    def test_macro_note_trop_longue_rejetee_en_422(self, client):
        response = client.post(
            "/webhook/tradingview",
            json=_payload(macro_level="HIGH", macro_note="x" * 101),
        )
        assert response.status_code == 422
