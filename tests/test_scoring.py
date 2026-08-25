"""Tests du score de qualité des signaux (Phase 26).

Le total est calculé côté backend à partir des composantes (jamais envoyé
par le client), chaque composante est bornée par le barème (Projet.md §40),
le score est stocké en base et affiché dans l'embed Discord comme indicateur
interne — jamais comme une probabilité de gain.
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.signals.scoring import SCORE_COMPONENT_MAX, compute_score
from app.signals.schemas import TradingViewSignal
from tests.test_multi import _payload, _signals_en_base


def _signal(**overrides) -> TradingViewSignal:
    return TradingViewSignal(**_payload(**overrides))


# --- Calcul du total ---

class TestComputeScore:
    def test_aucune_composante_donne_none(self):
        assert compute_score(_signal()) is None

    def test_total_sur_100_toutes_composantes(self):
        signal = _signal(
            score_trend=20,
            score_momentum=20,
            score_macd=15,
            score_volume=15,
            score_structure=20,
            score_htf=10,
        )
        assert compute_score(signal) == 100

    def test_composantes_absentes_valent_zero(self):
        """Une stratégie qui n'évalue pas le volume ni la structure : total < 100."""
        signal = _signal(score_trend=10, score_momentum=20, score_macd=8)
        assert compute_score(signal) == 38

    def test_bareme_somme_a_100(self):
        assert sum(SCORE_COMPONENT_MAX.values()) == 100


# --- Validation des bornes ---

class TestValidationScore:
    def test_composante_trop_grande_rejetee(self, client):
        response = client.post(
            "/webhook/tradingview", json=_payload(score_macd=16)
        )
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert response.json()["reason"] == "invalid_score"

    def test_composante_negative_rejetee(self, client):
        response = client.post(
            "/webhook/tradingview", json=_payload(score_trend=-1)
        )
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert response.json()["reason"] == "invalid_score"

    def test_borne_max_exacte_acceptee(self, client):
        response = client.post(
            "/webhook/tradingview",
            json=_payload(
                score_trend=20,
                score_momentum=20,
                score_macd=15,
                score_volume=15,
                score_structure=20,
                score_htf=10,
            ),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "sent"


# --- Pipeline complet : stockage + affichage ---

class TestScorePipeline:
    def test_score_stocke_en_base(self, client):
        client.post(
            "/webhook/tradingview",
            json=_payload(score_trend=20, score_momentum=10, score_htf=5),
        )
        signals = _signals_en_base(client)
        assert len(signals) == 1
        assert signals[0].score == 35

    def test_score_null_sans_composante(self, client):
        """Compatibilité : un signal sans score reste valide, score NULL en base."""
        client.post("/webhook/tradingview", json=_payload())
        signals = _signals_en_base(client)
        assert len(signals) == 1
        assert signals[0].score is None

    def test_embed_affiche_le_score(self, client):
        client.post(
            "/webhook/tradingview",
            json=_payload(score_trend=20, score_momentum=20, score_macd=15),
        )
        embed = client.notifier.sent[0]
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Signal Score"] == "55/100"
        # Projet.md §40 : jamais présenté comme une probabilité de gain.
        assert "probabilité" in (embed.footer.text or "")

    def test_embed_sans_score_sans_champ(self, client):
        client.post("/webhook/tradingview", json=_payload())
        embed = client.notifier.sent[0]
        noms = [f.name for f in embed.fields]
        assert "Signal Score" not in noms
        # discord.py renvoie toujours un EmbedProxy : on vérifie l'absence de texte.
        assert embed.footer.text is None

    def test_doublon_avec_score_ignored(self, client):
        """La déduplication est inchangée par le score (signal_uid identique)."""
        payload = _payload(score_trend=20)
        first = client.post("/webhook/tradingview", json=payload)
        second = client.post("/webhook/tradingview", json=payload)
        assert first.json()["status"] == "sent"
        assert second.json()["status"] == "duplicate"
        assert len(_signals_en_base(client)) == 1
        assert len(client.notifier.sent) == 1

    def test_coercition_chaines_tradingview(self, client):
        """TradingView envoie les nombres en texte : "20" doit être accepté."""
        response = client.post(
            "/webhook/tradingview",
            json=_payload(score_trend="20", score_momentum="10"),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "sent"
        assert _signals_en_base(client)[0].score == 30


# --- /lastsignal recharge le score depuis la base ---

class TestLastSignalScore:
    def test_lastsignal_renvoie_le_score(self, client):
        from app.database.models import Signal, Strategy
        from app.discord import embeds

        client.post(
            "/webhook/tradingview",
            json=_payload(score_trend=20, score_momentum=20, score_macd=15),
        )

        async def load():
            async with client.db_factory() as session:
                return (
                    await session.execute(select(Signal, Strategy.name).join(
                        Strategy, Signal.strategy_id == Strategy.id
                    ))
                ).first()

        signal, strategy_name = asyncio.run(load())
        embed = embeds.build_signal_embed_from_row(signal, strategy_name)
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Signal Score"] == "55/100"
