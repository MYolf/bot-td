"""Tests de POST /internal/prealert (pré-alertes du moteur local).

Frontière interne non fiable : secret (401), liste blanche des symboles,
aucune écriture en base (les pré-alertes ne sont pas des signaux), envoi
Discord best-effort via le notifieur dédié.
"""

import asyncio

import pytest

from app.services.discord_service import set_advance_notifier

from tests.conftest import FakeNotifier


@pytest.fixture
def advance_notifier() -> FakeNotifier:
    notifier = FakeNotifier()
    set_advance_notifier(notifier)
    yield notifier
    set_advance_notifier(None)  # global : toujours remis à zéro (leçon TP/SL)


def _advance_payload(**overrides) -> dict:
    payload = {
        "secret": "secret-test",
        "kind": "advance",
        "strategy": "momentum_v1",
        "symbol": "BTCUSDT",
        "timeframe": "15",
        "action": "BUY",
        "price": 77000.5,
        "stop_loss": 76230.5,
        "take_profit": 78540.5,
        "risk_reward": 2.0,
        "score_trend": 20,
        "score_momentum": 20,
        "score_macd": 15,
    }
    payload.update(overrides)
    return payload


class TestAuthentification:
    def test_secret_invalide_401(self, client, advance_notifier):
        response = client.post(
            "/internal/prealert", json=_advance_payload(secret="mauvais")
        )
        assert response.status_code == 401
        assert advance_notifier.sent == []

    def test_symbole_hors_liste_rejete(self, client, advance_notifier):
        response = client.post(
            "/internal/prealert", json=_advance_payload(symbol="DOGEUSDT")
        )
        assert response.status_code == 200
        assert response.json() == {"status": "rejected", "reason": "symbol_not_allowed"}
        assert advance_notifier.sent == []


class TestAdvance:
    def test_embed_envoye_sans_heure_ni_numero(self, client, advance_notifier):
        response = client.post("/internal/prealert", json=_advance_payload())
        assert response.status_code == 200
        assert response.json() == {"status": "sent"}
        (embed,) = advance_notifier.sent
        assert embed.title == "🟢 LONG SIGNAL — BTCUSDT"
        champs = {f.name: f.value for f in embed.fields}
        assert "Signal Time" not in champs  # bougie en formation : pas d'heure
        assert "Trade" not in champs  # rien en base, pas de numéro
        assert champs["Entry"] == "77,000.50 (limite)"
        assert champs["Signal Score"] == "100/100"  # 55/55 recalibré
        assert "limite" in embed.description
        # Footer : rappel que la pré-alerte n'est pas comptabilisée.
        assert embed.footer.text and "non comptabilisée" in embed.footer.text

    def test_aucune_ecriture_en_base(self, client, advance_notifier):
        from sqlalchemy import select

        from app.database.models import Signal

        client.post("/internal/prealert", json=_advance_payload())

        def count() -> int:
            async def load() -> int:
                async with client.db_factory() as session:
                    return len(
                        (await session.execute(select(Signal))).scalars().all()
                    )

            return asyncio.run(load())

        assert count() == 0

    def test_sans_sl_tp_rejete_en_validation(self, client, advance_notifier):
        payload = _advance_payload()
        del payload["stop_loss"], payload["take_profit"]
        response = client.post("/internal/prealert", json=payload)
        assert response.status_code == 422
        assert advance_notifier.sent == []


class TestInvalidated:
    def test_embed_annulation(self, client, advance_notifier):
        response = client.post(
            "/internal/prealert",
            json=_advance_payload(
                kind="invalidated", action="SELL", price=77000.5
            ),
        )
        assert response.status_code == 200
        assert response.json() == {"status": "sent"}
        (embed,) = advance_notifier.sent
        assert embed.title == "❌ Pré-alerte annulée — BTCUSDT"
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Direction"] == "SHORT"
        assert champs["Niveau touché"] == "77,000.50"
        assert "déchargez la position" in embed.description


class TestAnticipatif:
    """Mode anticipatif : expires_in + touch_rate rendus dans l'embed."""

    def test_champs_validite_et_taux(self, client, advance_notifier):
        response = client.post(
            "/internal/prealert",
            json=_advance_payload(expires_in=2, touch_rate=0.70),
        )
        assert response.status_code == 200
        assert response.json() == {"status": "sent"}
        (embed,) = advance_notifier.sent
        champs = {f.name: f.value for f in embed.fields}
        assert "Expire dans 2 bougie(s) (≈ 30 min)" in champs["Validité"]
        assert embed.footer.text and "~70 %" in embed.footer.text
        assert "ni probabilité de gain" in embed.footer.text
        # Description anticipative : message « Signal validé » annoncé.
        assert "Signal validé" in embed.description

    def test_expires_in_invalide_422(self, client, advance_notifier):
        response = client.post(
            "/internal/prealert", json=_advance_payload(expires_in=0)
        )
        assert response.status_code == 422
        assert advance_notifier.sent == []

    def test_touch_rate_hors_bornes_422(self, client, advance_notifier):
        response = client.post(
            "/internal/prealert", json=_advance_payload(expires_in=2, touch_rate=1.5)
        )
        assert response.status_code == 422
        assert advance_notifier.sent == []


class TestConfirmed:
    def test_embed_signal_valide(self, client, advance_notifier):
        response = client.post(
            "/internal/prealert",
            json=_advance_payload(kind="confirmed", price=77000.5),
        )
        assert response.status_code == 200
        assert response.json() == {"status": "sent"}
        (embed,) = advance_notifier.sent
        assert embed.title == "✅ Signal validé — BTCUSDT"
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Direction"] == "LONG"
        assert champs["Niveau d'entrée"] == "77,000.50"
        # Honnêteté : l'avantage de fill est annoncé ≈ neutre.
        assert embed.footer.text and "neutre" in embed.footer.text


class TestExpired:
    def test_embed_expiration(self, client, advance_notifier):
        response = client.post(
            "/internal/prealert",
            json=_advance_payload(
                kind="expired", action="SELL", price=77000.5, expires_in=2
            ),
        )
        assert response.status_code == 200
        assert response.json() == {"status": "sent"}
        (embed,) = advance_notifier.sent
        assert embed.title == "⌛ Pré-alerte expirée — BTCUSDT"
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Direction"] == "SHORT"
        assert champs["Niveau non atteint"] == "77,000.50"
        assert champs["Validité écoulée"] == "2 bougie(s)"
        assert "retirez-le" in embed.description

    def test_kind_inconnu_422(self, client, advance_notifier):
        response = client.post(
            "/internal/prealert", json=_advance_payload(kind="autre")
        )
        assert response.status_code == 422
        assert advance_notifier.sent == []


class TestSansSalon:
    def test_notifieur_absent_statut_ignore(self, client):
        set_advance_notifier(None)
        response = client.post("/internal/prealert", json=_advance_payload())
        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "reason": "no_channel"}
