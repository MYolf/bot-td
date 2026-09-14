"""Tests des signaux à l'avance (engine/advance.py + intégration runner).

Deux niveaux :
- fonctions pures : plan_advance (proximité du niveau P*, filtre score,
  barème SL/TP) sur un historique réaliste (marche aléatoire en régimes) ;
- runner : annonce envoyée une seule fois par bougie en formation,
  résolution à la clôture (confirmé = silence, touché non confirmé =
  annulation, jamais touché = silence).
"""

import pytest

import engine.runner as runner_module
from engine.advance import (
    AdvancePlan,
    build_advance_payload,
    build_invalidation_payload,
    forming_state,
    plan_advance,
)
from engine.config import EngineSettings
from engine.runner import SignalEngine
from engine.strategy import Candle, MomentumParams
from engine.touch_study import bullish_at, trigger_level
from tests.test_engine_runner import (  # noqa: F401 (fixture partagée)
    FakeFetcher,
    FakeSender,
    engine_settings,
    make_candles,
)
from tests.test_touch_study import _random_walk_candles

EPS = 0.0015


def _forming(open_time: int, high: float, low: float) -> Candle:
    return Candle(
        open_time=open_time,
        close_time=open_time + 900_000 - 1,
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
        volume=1.0,
    )


def _trouve_contexte_buy() -> tuple[list[Candle], Candle, float]:
    """Historique + bougie en formation proche du niveau P* BUY.

    Cherche (graines déterministes) une dernière bougie fermée où :
    - le niveau P* BUY existe (trigger_level) ;
    - BUY n'est pas déjà vrai à la clôture précédente (transition possible).
    La bougie en formation est placée à mi-chemin de eps : proche (probe
    passe), pas encore touchée.
    """
    params = MomentumParams()
    for seed in range(60):
        candles = _random_walk_candles(400, seed=seed)
        st = forming_state(candles, params)
        if st is None:
            continue
        if bullish_at(st, st.prev_close, params):
            continue  # déjà tout haussier : pas de transition
        level = trigger_level(st, params, "BUY")
        if level is None:
            continue
        high = level * (1 - EPS / 2)
        if high <= candles[-1].close:
            continue  # haut de bougie irréaliste (niveau sous le prix)
        forming = _forming(candles[-1].open_time + 900_000, high, candles[-1].low)
        return candles, forming, level
    raise AssertionError("aucun contexte BUY trouvé (graines 0..59)")


class TestPlanAdvance:
    def test_plan_emis_quand_le_prix_apprivoche_le_niveau(self):
        params = MomentumParams()
        candles, forming, level = _trouve_contexte_buy()
        plan = plan_advance(candles, forming, params, min_score=0, eps=EPS)
        assert plan is not None
        assert plan.action == "BUY"
        assert abs(plan.level - level) / level < 1e-6
        # Même barème que le moteur : SL/TP relatifs au niveau d'entrée.
        assert abs(plan.stop_loss - level * (1 - params.sl_pct)) < 1e-9
        assert abs(plan.take_profit - level * (1 + params.tp_pct)) < 1e-9
        assert abs(plan.risk_reward - params.tp_pct / params.sl_pct) < 1e-9
        assert 43 <= plan.score_trend + plan.score_momentum + plan.score_macd <= 55

    def test_aucun_plan_si_le_prix_est_loin_du_niveau(self):
        params = MomentumParams()
        candles, _, level = _trouve_contexte_buy()
        # Haut de bougie loin SOUS le niveau : probe = high*(1+eps) ne
        # confirme pas, pas d'annonce.
        forming = _forming(
            candles[-1].open_time + 900_000, level * (1 - 0.01), candles[-1].low
        )
        assert plan_advance(candles, forming, params, eps=EPS) is None

    def test_filtre_score_au_niveau(self):
        params = MomentumParams()
        candles, forming, _ = _trouve_contexte_buy()
        # min_score impossible (max 55) : même proche du niveau, rien.
        assert plan_advance(candles, forming, params, min_score=56, eps=EPS) is None

    def test_historique_insuffisant_aucun_plan(self):
        forming = _forming(1_900_000, 101.0, 99.0)
        assert plan_advance(make_candles(50), forming, MomentumParams()) is None


class TestPayloads:
    def test_payload_advance_puis_invalide(self):
        plan = AdvancePlan(
            action="SELL",
            level=100.0,
            stop_loss=101.0,
            take_profit=98.0,
            risk_reward=2.0,
            score_trend=20,
            score_momentum=20,
            score_macd=15,
        )
        payload = build_advance_payload(
            plan, symbol="BTCUSDT", timeframe="15", secret="s3cret"
        )
        assert payload["kind"] == "advance"
        assert payload["strategy"] == "momentum_v1"
        assert payload["action"] == "SELL"
        assert payload["price"] == 100.0
        assert payload["score_macd"] == 15
        invalide = build_invalidation_payload(
            symbol="BTCUSDT", timeframe="15", secret="s3cret",
            action="SELL", level=100.0,
        )
        assert invalide["kind"] == "invalidated"
        assert invalide["price"] == 100.0


# ---------------------------------------------------------------- runner --


class FakeFormingFetcher:
    """Bougie en formation scriptée (une réponse par appel)."""

    def __init__(self, responses: list):
        self._responses = list(responses)

    async def __call__(self, symbol: str, timeframe: str) -> Candle | None:
        response = self._responses.pop(0) if self._responses else None
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def plan_buy() -> AdvancePlan:
    return AdvancePlan(
        action="BUY",
        level=101.0,
        stop_loss=99.99,
        take_profit=103.02,
        risk_reward=2.0,
        score_trend=20,
        score_momentum=20,
        score_macd=15,
    )


async def test_annonce_unique_puis_annulation_si_touche_non_confirme(
    engine_settings, monkeypatch, plan_buy
) -> None:
    """Cycle complet : annonce -> même bougie (rien) -> clôture touchée sans
    signal officiel -> une annulation, une seule."""
    plans = [plan_buy, None, None]  # 1re annonce, puis plus rien
    monkeypatch.setattr(
        runner_module,
        "plan_advance",
        lambda *a, **k: plans.pop(0) if plans else None,
    )
    # Aucune transition officielle dans ce scénario (données plates).
    monkeypatch.setattr(runner_module, "evaluate_momentum_v1", lambda c, p: None)
    base = make_candles(300)  # dernière fermée : open 1_000_000 + 299*900k
    dernier_open = base[-1].open_time
    fermee = Candle(  # la bougie annoncée, fermée : high >= niveau -> touchée
        open_time=dernier_open + 900_000,
        close_time=dernier_open + 2 * 900_000 - 1,
        open=100.0, high=101.5, low=99.5, close=100.5, volume=1.0,
    )
    fermes = base + [fermee]
    forming_annoncee = _forming(fermee.open_time, 100.9, 99.8)
    forming_suivante = _forming(fermee.open_time + 900_000, 100.0, 99.0)

    fetcher = FakeFetcher([base, base, fermes, fermes])
    forming_fetcher = FakeFormingFetcher(
        [forming_annoncee, forming_annoncee, forming_suivante, forming_suivante]
    )
    sender = FakeSender()
    advance_sender = FakeSender()
    engine = SignalEngine(
        engine_settings, fetcher, sender,
        advance_sender=advance_sender, forming_fetcher=forming_fetcher,
    )

    await engine.poll_once()  # premier relevé + annonce de la bougie en formation
    await engine.poll_once()  # même bougie fermée : pas de nouvelle annonce
    await engine.poll_once()  # la bougie annoncée se ferme (non confirmée, touchée)
    await engine.poll_once()  # clôture traitée : plus rien

    annonces = [p for p in advance_sender.payloads if p["kind"] == "advance"]
    annulations = [p for p in advance_sender.payloads if p["kind"] == "invalidated"]
    assert len(annonces) == 1
    assert annonces[0]["symbol"] == "BTCUSDT"
    assert annonces[0]["action"] == "BUY"
    assert annonces[0]["price"] == pytest.approx(101.0)
    assert len(annulations) == 1
    assert annulations[0]["price"] == pytest.approx(101.0)
    # Le pipeline officiel n'est pas touché : aucun signal webhook.
    assert sender.payloads == []


async def test_silence_si_signal_officiel_emis(
    engine_settings, monkeypatch, plan_buy
) -> None:
    """Confirmé par le signal officiel : pas d'annulation."""
    monkeypatch.setattr(
        runner_module, "plan_advance", lambda *a, **k: None
    )
    # Annonce manuelle : on simule qu'elle a eu lieu au cycle précédent.
    base = make_candles(300)
    dernier_open = base[-1].open_time
    fermee = Candle(
        open_time=dernier_open + 900_000,
        close_time=dernier_open + 2 * 900_000 - 1,
        open=100.0, high=101.5, low=99.5, close=100.5, volume=1.0,
    )
    fermes = base + [fermee]
    fetcher = FakeFetcher([base, fermes])
    advance_sender = FakeSender()
    engine = SignalEngine(
        engine_settings, fetcher, FakeSender(), advance_sender=advance_sender,
        forming_fetcher=FakeFormingFetcher([]),
    )
    await engine.poll_once()  # premier relevé
    engine._advance_pending["BTCUSDT"] = {
        "open_time": fermee.open_time,
        "action": "BUY",
        "level": 101.0,
    }
    # Le moteur émet le signal officiel pour cette même bougie et ce sens.
    import dataclasses

    from tests.test_engine_runner import fake_signal_result

    result = dataclasses.replace(
        fake_signal_result("BUY"), candle_open_time=fermee.open_time
    )

    async def sender_officiel(payload):
        engine._advance_emitted["BTCUSDT"] = (
            result.candle_open_time,
            result.action,
        )
        return {"status": "sent"}

    engine._sender = sender_officiel
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: result
    )
    await engine.poll_once()

    assert advance_sender.payloads == []  # confirmé : silence


async def test_silence_si_le_niveau_n_est_jamais_touche(
    engine_settings, monkeypatch
) -> None:
    """La bougie annoncée clôture sans atteindre le niveau : pas de message."""
    monkeypatch.setattr(runner_module, "plan_advance", lambda *a, **k: None)
    base = make_candles(300)
    dernier_open = base[-1].open_time
    fermee = Candle(  # high = 100 << niveau 101 : jamais touchée
        open_time=dernier_open + 900_000,
        close_time=dernier_open + 2 * 900_000 - 1,
        open=100.0, high=100.0, low=99.0, close=100.0, volume=1.0,
    )
    fetcher = FakeFetcher([base, base + [fermee]])
    advance_sender = FakeSender()
    engine = SignalEngine(
        engine_settings, fetcher, FakeSender(), advance_sender=advance_sender,
        forming_fetcher=FakeFormingFetcher([]),
    )
    await engine.poll_once()
    engine._advance_pending["BTCUSDT"] = {
        "open_time": fermee.open_time,
        "action": "BUY",
        "level": 101.0,
    }
    await engine.poll_once()
    assert advance_sender.payloads == []


async def test_pas_d_annonce_si_position_meme_sens_ouverte(
    engine_settings, monkeypatch, plan_buy
) -> None:
    """pyramiding=0 : un BUY ne serait jamais émis, la pré-alerte serait du
    bruit — plan_advance dit BUY mais would_fill(BUY) est faux."""
    plans = [None, plan_buy, plan_buy]  # rien au premier relevé, puis BUY
    monkeypatch.setattr(
        runner_module,
        "plan_advance",
        lambda *a, **k: plans.pop(0) if plans else None,
    )
    base = make_candles(300)
    dernier_open = base[-1].open_time
    forming = _forming(dernier_open + 900_000, 100.9, 99.8)
    fetcher = FakeFetcher([base, base])
    advance_sender = FakeSender()
    engine = SignalEngine(
        engine_settings, fetcher, FakeSender(), advance_sender=advance_sender,
        forming_fetcher=FakeFormingFetcher([forming, forming]),
    )
    await engine.poll_once()  # premier relevé : pas d'annonce (plan None)
    # Position simulée BUY déjà ouverte (comme si l'historique l'avait créée).
    engine._trackers["BTCUSDT"].open("BUY", 100.0, 99.0, 102.0)
    await engine.poll_once()
    assert advance_sender.payloads == []


async def test_envoi_echoue_pas_de_pending(
    engine_settings, monkeypatch, plan_buy
) -> None:
    """Si l'envoi de la pré-alerte échoue, rien n'est mémorisé (le cycle
    suivant pourra réannoncer pour la même bougie)."""

    class SenderQuiEchoue:
        async def __call__(self, payload):
            raise RuntimeError("backend injoignable")

    monkeypatch.setattr(
        runner_module, "plan_advance", lambda *a, **k: plan_buy
    )
    base = make_candles(300)
    forming = _forming(base[-1].open_time + 900_000, 100.9, 99.8)
    engine = SignalEngine(
        engine_settings,
        FakeFetcher([base, base]),
        FakeSender(),
        advance_sender=SenderQuiEchoue(),
        forming_fetcher=FakeFormingFetcher([forming, forming]),
    )
    await engine.poll_once()
    await engine.poll_once()
    assert engine._advance_pending == {}
