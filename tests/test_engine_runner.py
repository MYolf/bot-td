"""Tests de la boucle du moteur (engine/runner.py).

Dépendances réelles remplacées par des fakes : aucun réseau, aucun envoi.
"""

from datetime import datetime, timedelta, timezone

import pytest

import engine.runner as runner_module
from engine.config import EngineSettings
from engine.macro.models import MacroEvent
from engine.macro.risk_engine import MacroGate
from engine.runner import SignalEngine
from engine.strategy import Candle, SignalResult

INTERVAL_MS = 900_000


class FakeFetcher:
    """Retourne des réponses scriptées, enregistre les appels."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, int]] = []

    async def __call__(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        self.calls.append((symbol, timeframe, limit))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeSender:
    def __init__(self):
        self.payloads: list[dict] = []

    async def __call__(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return {"status": "sent"}


def make_candles(n: int, start_open: int = 1_000_000) -> list[Candle]:
    return [
        Candle(
            open_time=start_open + i * INTERVAL_MS,
            close_time=start_open + (i + 1) * INTERVAL_MS - 1,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1.0,
        )
        for i in range(n)
    ]


def fake_signal_result(action: str = "BUY") -> SignalResult:
    return SignalResult(
        action=action,
        entry=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        score_trend=20,
        score_momentum=20,
        score_macd=15,
        candle_open_time=0,
        candle_close_time=INTERVAL_MS,
    )


@pytest.fixture
def engine_settings(monkeypatch) -> EngineSettings:
    monkeypatch.setenv("ENGINE_SYMBOLS", '["BTCUSDT"]')
    monkeypatch.setenv("ENGINE_TIMEFRAME", "15")
    monkeypatch.setenv("ENGINE_POLL_SECONDS", "1")
    monkeypatch.setenv("ENGINE_CANDLE_LIMIT", "300")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "secret-test")
    return EngineSettings(_env_file=None)


async def test_premier_releve_sans_evaluation_ni_envoi(engine_settings) -> None:
    candles = make_candles(300)
    fetcher = FakeFetcher([candles])
    sender = FakeSender()
    engine = SignalEngine(engine_settings, fetcher, sender)

    await engine.poll_once()

    assert len(fetcher.calls) == 1
    assert sender.payloads == []


async def test_meme_bougie_pas_de_nouvelle_evaluation(engine_settings) -> None:
    candles = make_candles(300)
    fetcher = FakeFetcher([candles, candles])
    sender = FakeSender()
    engine = SignalEngine(engine_settings, fetcher, sender)

    await engine.poll_once()
    await engine.poll_once()  # même open_time -> pas d'évaluation

    assert sender.payloads == []


async def test_nouvelle_bougie_avec_signal_envoye_une_fois(
    engine_settings, monkeypatch
) -> None:
    candles = make_candles(300)
    nouvelle = make_candles(301, start_open=1_000_000)  # +1 bougie fermée
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: fake_signal_result()
    )
    fetcher = FakeFetcher([candles, nouvelle, nouvelle])
    sender = FakeSender()
    engine = SignalEngine(engine_settings, fetcher, sender)

    await engine.poll_once()  # premier relevé : rien
    await engine.poll_once()  # nouvelle bougie + transition -> envoi
    await engine.poll_once()  # même bougie -> rien

    assert len(sender.payloads) == 1
    payload = sender.payloads[0]
    assert payload["symbol"] == "BTCUSDT"
    assert payload["timeframe"] == "15"
    assert payload["strategy"] == "momentum_v1"
    assert payload["secret"] == "secret-test"
    assert payload["action"] == "BUY"


async def test_sans_transition_aucun_envoi(engine_settings) -> None:
    candles = make_candles(300)
    nouvelle = make_candles(301)
    fetcher = FakeFetcher([candles, nouvelle])
    sender = FakeSender()
    engine = SignalEngine(engine_settings, fetcher, sender)

    await engine.poll_once()
    await engine.poll_once()  # évaluation réelle : série plate -> None

    assert sender.payloads == []


async def test_erreur_fetch_ne_fait_pas_planter_le_cycle(engine_settings) -> None:
    candles = make_candles(300)
    fetcher = FakeFetcher([RuntimeError("Binance indisponible"), candles])
    sender = FakeSender()
    engine = SignalEngine(engine_settings, fetcher, sender)

    await engine.poll_once()  # l'erreur est absorbée, pas de crash
    await engine.poll_once()  # premier relevé effectif

    assert sender.payloads == []


async def test_erreur_envoi_absorbee(engine_settings, monkeypatch) -> None:
    candles = make_candles(300)
    nouvelle = make_candles(301)
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: fake_signal_result()
    )

    class SenderEnEchec:
        async def __call__(self, payload: dict) -> dict:
            raise RuntimeError("webhook injoignable (test)")

    fetcher = FakeFetcher([candles, nouvelle])
    engine = SignalEngine(engine_settings, fetcher, SenderEnEchec())

    await engine.poll_once()
    await engine.poll_once()  # envoi en échec : loggé, pas de crash


async def test_signal_meme_sens_ignore_pyramiding_zero(
    engine_settings, monkeypatch
) -> None:
    """Fidélité TradingView : pas d'alerte si un ordre simulé ne se serait
    pas exécuté (position déjà ouverte dans le même sens)."""
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: fake_signal_result("BUY")
    )
    fetcher = FakeFetcher(
        [
            make_candles(300),   # premier relevé : reconstruction, rien
            make_candles(301),   # BUY -> émis (plat -> ouverture)
            make_candles(302),   # BUY -> IGNORÉ (déjà long, pyramiding = 0)
            make_candles(303),   # BUY -> toujours ignoré
        ]
    )
    sender = FakeSender()
    engine = SignalEngine(engine_settings, fetcher, sender)

    for _ in range(4):
        await engine.poll_once()

    assert len(sender.payloads) == 1


async def test_signal_oppose_emis_en_renversement(engine_settings, monkeypatch) -> None:
    actions = iter(["BUY", "SELL"])
    monkeypatch.setattr(
        runner_module,
        "evaluate_momentum_v1",
        lambda c, p: fake_signal_result(next(actions)),
    )
    fetcher = FakeFetcher(
        [make_candles(300), make_candles(301), make_candles(302)]
    )
    sender = FakeSender()
    engine = SignalEngine(engine_settings, fetcher, sender)

    for _ in range(3):
        await engine.poll_once()

    assert [p["action"] for p in sender.payloads] == ["BUY", "SELL"]


# --- Price updates (bougie fermée -> POST /internal/prices) ---

async def test_price_update_envoye_a_chaque_nouvelle_bougie(engine_settings) -> None:
    nouvelle = make_candles(301)
    fetcher = FakeFetcher([make_candles(300), nouvelle, make_candles(302)])
    price_sender = FakeSender()
    engine = SignalEngine(
        engine_settings, fetcher, FakeSender(), price_sender=price_sender
    )

    await engine.poll_once()  # premier relevé : rien (reconstruction)
    await engine.poll_once()  # nouvelle bougie -> price update
    await engine.poll_once()  # nouvelle bougie -> price update

    assert len(price_sender.payloads) == 2
    payload = price_sender.payloads[0]
    derniere = nouvelle[-1]
    assert payload["symbol"] == "BTCUSDT"
    assert payload["timeframe"] == "15"
    assert payload["open_time"] == derniere.open_time
    assert payload["high"] == derniere.high
    assert payload["low"] == derniere.low
    assert payload["secret"] == "secret-test"


async def test_premier_releve_nenvoie_pas_de_price_update(engine_settings) -> None:
    fetcher = FakeFetcher([make_candles(300)])
    price_sender = FakeSender()
    engine = SignalEngine(engine_settings, fetcher, FakeSender(), price_sender=price_sender)

    await engine.poll_once()

    assert price_sender.payloads == []


async def test_price_update_sans_envoyeur_nenvoie_rien(engine_settings) -> None:
    fetcher = FakeFetcher([make_candles(300), make_candles(301)])
    engine = SignalEngine(engine_settings, fetcher, FakeSender())  # sans price_sender

    await engine.poll_once()
    await engine.poll_once()  # ne doit pas lever


async def test_echec_price_update_absorbe(engine_settings) -> None:
    class PriceSenderEnEchec:
        def __init__(self):
            self.appels = 0

        async def __call__(self, payload: dict) -> dict:
            self.appels += 1
            raise RuntimeError("backend injoignable (test)")

    price_sender = PriceSenderEnEchec()
    fetcher = FakeFetcher([make_candles(300), make_candles(301)])
    engine = SignalEngine(engine_settings, fetcher, FakeSender(), price_sender=price_sender)

    await engine.poll_once()
    await engine.poll_once()  # échec loggé, pas de crash

    assert price_sender.appels == 1


# ------------------------------------------ filtre qualité ENGINE_MIN_SCORE --


def _fake_result(action: str = "BUY", trend: int = 10, momentum: int = 10, macd: int = 8) -> SignalResult:
    """Signal dont le score total est paramétrable (28 par défaut)."""
    return SignalResult(
        action=action,
        entry=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        score_trend=trend,
        score_momentum=momentum,
        score_macd=macd,
        candle_open_time=0,
        candle_close_time=INTERVAL_MS,
    )


async def test_signal_sous_le_seuil_non_emis_ni_ouvert(engine_settings, monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_MIN_SCORE", "45")
    settings = EngineSettings(_env_file=None)
    candles = make_candles(300)
    nouvelle = make_candles(301, start_open=1_000_000)
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: _fake_result()  # total 28
    )
    fetcher = FakeFetcher([candles, nouvelle])
    sender = FakeSender()
    engine = SignalEngine(settings, fetcher, sender)

    await engine.poll_once()  # premier relevé (reconstruction)
    await engine.poll_once()  # transition score 28 < 45

    assert sender.payloads == []
    # La position simulée n'est PAS ouverte : un signal postérieur de qualité
    # dans le même sens restera émissible.
    assert engine._trackers["BTCUSDT"].position is None


async def test_signal_au_seuil_emis(engine_settings, monkeypatch) -> None:
    monkeypatch.setenv("ENGINE_MIN_SCORE", "45")
    settings = EngineSettings(_env_file=None)
    candles = make_candles(300)
    nouvelle = make_candles(301, start_open=1_000_000)
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: fake_signal_result()  # 55
    )
    fetcher = FakeFetcher([candles, nouvelle])
    sender = FakeSender()
    engine = SignalEngine(settings, fetcher, sender)

    await engine.poll_once()
    await engine.poll_once()

    assert len(sender.payloads) == 1
    assert engine._trackers["BTCUSDT"].position is not None


async def test_seuil_zero_tout_emis(engine_settings, monkeypatch) -> None:
    # Comportement historique inchangé : 0 = aucun filtrage.
    candles = make_candles(300)
    nouvelle = make_candles(301, start_open=1_000_000)
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: _fake_result()  # 28
    )
    fetcher = FakeFetcher([candles, nouvelle])
    sender = FakeSender()
    engine = SignalEngine(engine_settings, fetcher, sender)

    await engine.poll_once()
    await engine.poll_once()

    assert len(sender.payloads) == 1


# --- Macro display-only (MACRO.md §10) : annotation, jamais blocage ---

# Timestamp du signal fake : candle_close_time = 900 000 ms = 00:15:00Z.
_TS_SIGNAL = datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc)


def _gate(minutes_offset: float, event_type: str = "CPI") -> MacroGate:
    """Événement placé à minutes_offset du timestamp du signal fake."""
    at = _TS_SIGNAL + timedelta(minutes=minutes_offset)
    return MacroGate([MacroEvent(event_type=event_type, scheduled_at=at)])


async def test_macro_extreme_annote_le_payload(engine_settings, monkeypatch) -> None:
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: fake_signal_result()
    )
    fetcher = FakeFetcher([make_candles(300), make_candles(301)])
    sender = FakeSender()
    # CPI imminent (T-8) : EXTREME, mais le signal part quand même.
    engine = SignalEngine(engine_settings, fetcher, sender, macro=_gate(8))

    await engine.poll_once()
    await engine.poll_once()

    assert len(sender.payloads) == 1
    payload = sender.payloads[0]
    assert payload["macro_level"] == "EXTREME"
    assert payload["macro_note"] == "CPI dans 8 min"


async def test_macro_high_annote_le_payload(engine_settings, monkeypatch) -> None:
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: fake_signal_result()
    )
    fetcher = FakeFetcher([make_candles(300), make_candles(301)])
    sender = FakeSender()
    # NFP à T-20 : HIGH (entre EXTREME pré = 15 min et HIGH pré = 30 min).
    engine = SignalEngine(engine_settings, fetcher, sender, macro=_gate(20, "NFP"))

    await engine.poll_once()
    await engine.poll_once()

    payload = sender.payloads[0]
    assert payload["macro_level"] == "HIGH"
    assert payload["macro_note"] == "NFP dans 20 min"


async def test_macro_low_aucune_annotation(engine_settings, monkeypatch) -> None:
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: fake_signal_result()
    )
    fetcher = FakeFetcher([make_candles(300), make_candles(301)])
    sender = FakeSender()
    # Événement 5 h plus tard : LOW -> JSON strictement identique à avant.
    engine = SignalEngine(engine_settings, fetcher, sender, macro=_gate(300))

    await engine.poll_once()
    await engine.poll_once()

    payload = sender.payloads[0]
    assert "macro_level" not in payload
    assert "macro_note" not in payload


async def test_macro_unknown_aucune_annotation(engine_settings, monkeypatch) -> None:
    """Failsafe : planning indisponible -> UNKNOWN -> signal normal."""
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: fake_signal_result()
    )
    fetcher = FakeFetcher([make_candles(300), make_candles(301)])
    sender = FakeSender()
    engine = SignalEngine(
        engine_settings, fetcher, sender, macro=MacroGate.disabled()
    )

    await engine.poll_once()
    await engine.poll_once()

    payload = sender.payloads[0]
    assert "macro_level" not in payload
    assert "macro_note" not in payload


async def test_macro_type_hors_perimetre_ignore(engine_settings, monkeypatch) -> None:
    """PPI exclu de l'affichage (MACRO.md §10) : jamais annoté."""
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: fake_signal_result()
    )
    fetcher = FakeFetcher([make_candles(300), make_candles(301)])
    sender = FakeSender()
    # PPI imminent (serait EXTREME) mais filtré par le périmètre MACRO_TYPES.
    gate = MacroGate(
        [MacroEvent(event_type="PPI", scheduled_at=_TS_SIGNAL + timedelta(minutes=8))],
        types={"FOMC", "CPI", "NFP"},
    )
    engine = SignalEngine(engine_settings, fetcher, sender, macro=gate)

    await engine.poll_once()
    await engine.poll_once()

    payload = sender.payloads[0]
    assert "macro_level" not in payload


async def test_sans_gate_payload_inchange(engine_settings, monkeypatch) -> None:
    """MACRO_ENABLED=false (défaut) : le moteur se comporte exactement comme
    avant l'existence du Macro Risk Engine."""
    monkeypatch.setattr(
        runner_module, "evaluate_momentum_v1", lambda c, p: fake_signal_result()
    )
    fetcher = FakeFetcher([make_candles(300), make_candles(301)])
    sender = FakeSender()
    engine = SignalEngine(engine_settings, fetcher, sender)

    await engine.poll_once()
    await engine.poll_once()

    payload = sender.payloads[0]
    assert set(payload) == {
        "secret", "strategy", "symbol", "exchange", "timeframe", "action",
        "price", "stop_loss", "take_profit", "score_trend", "score_momentum",
        "score_macd", "timestamp",
    }
