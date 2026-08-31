"""Tests de la boucle du moteur (engine/runner.py).

Dépendances réelles remplacées par des fakes : aucun réseau, aucun envoi.
"""

import pytest

import engine.runner as runner_module
from engine.config import EngineSettings
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
