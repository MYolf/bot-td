"""Tests de l'endpoint interne POST /internal/prices (clôture TP/SL à la bougie).

Catalogue : auth 401, symbole non autorisé, TP/SL détectés sur la bougie,
priorité SL si les deux touchés, anti-lookahead (bougie du signal ignorée),
notification Discord de clôture, position intacte si la bougie ne touche rien.
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.database.models import PaperPosition, PaperTrade
from app.services.discord_service import provide_recap_notifier

from tests.conftest import FakeNotifier

SECRET = "secret-test"


def _signal_payload(
    *,
    action: str = "BUY",
    price: str = "100",
    stop_loss: str = "98",
    take_profit: str = "104",
    timestamp: datetime | None = None,
    symbol: str = "BTCUSDT",
) -> dict:
    return {
        "secret": SECRET,
        "strategy": "momentum_v1",
        "symbol": symbol,
        "exchange": "BINANCE",
        "timeframe": "15",
        "action": action,
        "price": price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
    }


def _price_payload(
    *,
    high: float = 100.0,
    low: float = 100.0,
    open_time_ms: int,
    symbol: str = "BTCUSDT",
    secret: str = SECRET,
) -> dict:
    return {
        "secret": secret,
        "symbol": symbol,
        "timeframe": "15",
        "open_time": open_time_ms,
        "open": 100.0,
        "high": high,
        "low": low,
        "close": 100.0,
    }


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _positions(client) -> list[PaperPosition]:
    async def load():
        async with client.db_factory() as session:
            return list((await session.execute(select(PaperPosition))).scalars().all())

    return asyncio.run(load())


def _trades(client) -> list[PaperTrade]:
    async def load():
        async with client.db_factory() as session:
            return list((await session.execute(select(PaperTrade))).scalars().all())

    return asyncio.run(load())


class TestAuthEtValidation:
    def test_secret_invalide_401(self, client):
        client.post("/webhook/tradingview", json=_signal_payload())
        response = client.post(
            "/internal/prices",
            json=_price_payload(open_time_ms=_ms(datetime.now(timezone.utc)), secret="mauvais"),
        )
        assert response.status_code == 401
        # Rien n'a été clôturé.
        assert all(p.status == "OPEN" for p in _positions(client))

    def test_symbole_non_autorise_rejete(self, client):
        response = client.post(
            "/internal/prices",
            json=_price_payload(symbol="XYZUSDT", open_time_ms=_ms(datetime.now(timezone.utc))),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"

    def test_prix_invalide_422(self, client):
        payload = _price_payload(open_time_ms=_ms(datetime.now(timezone.utc)))
        payload["high"] = -1
        response = client.post("/internal/prices", json=payload)
        assert response.status_code == 422

    def test_aucune_position_reponse_ok(self, client):
        response = client.post(
            "/internal/prices",
            json=_price_payload(open_time_ms=_ms(datetime.now(timezone.utc))),
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "closed": 0}


class TestClotureParBougie:
    def test_buy_tp_atteint_par_le_high(self, client):
        signal_ts = datetime.now(timezone.utc)
        client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
        bougie_suivante = _ms(signal_ts + timedelta(minutes=1))

        response = client.post(
            "/internal/prices",
            json=_price_payload(high=104.5, low=99.0, open_time_ms=bougie_suivante),
        )

        assert response.json()["closed"] == 1
        (position,) = _positions(client)
        assert position.status == "CLOSED"
        assert position.result_r == Decimal("2")  # RR = 2 -> +2R
        (trade,) = _trades(client)
        assert trade.exit_reason == "TP"
        assert trade.exit_price == Decimal("104")  # sortie au niveau du TP

    def test_buy_sl_atteint_par_le_low(self, client):
        signal_ts = datetime.now(timezone.utc)
        client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))

        response = client.post(
            "/internal/prices",
            json=_price_payload(high=101.0, low=97.5, open_time_ms=_ms(signal_ts + timedelta(minutes=1))),
        )

        assert response.json()["closed"] == 1
        (position,) = _positions(client)
        assert position.result_r == Decimal("-1")
        (trade,) = _trades(client)
        assert trade.exit_reason == "SL"
        assert trade.exit_price == Decimal("98")

    def test_sl_prioritaire_si_les_deux_touches(self, client):
        signal_ts = datetime.now(timezone.utc)
        client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))

        response = client.post(
            "/internal/prices",
            json=_price_payload(high=105.0, low=97.0, open_time_ms=_ms(signal_ts + timedelta(minutes=1))),
        )

        assert response.json()["closed"] == 1
        (trade,) = _trades(client)
        assert trade.exit_reason == "SL"  # hypothèse prudente, fidèle au moteur

    def test_sell_tp_atteint_par_le_low(self, client):
        signal_ts = datetime.now(timezone.utc)
        client.post(
            "/webhook/tradingview",
            json=_signal_payload(
                action="SELL", stop_loss="102", take_profit="96", timestamp=signal_ts
            ),
        )

        response = client.post(
            "/internal/prices",
            json=_price_payload(high=101.0, low=95.5, open_time_ms=_ms(signal_ts + timedelta(minutes=1))),
        )

        assert response.json()["closed"] == 1
        (trade,) = _trades(client)
        assert trade.exit_reason == "TP"
        assert trade.exit_price == Decimal("96")

    def test_bougie_neutre_position_intacte(self, client):
        signal_ts = datetime.now(timezone.utc)
        client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))

        response = client.post(
            "/internal/prices",
            json=_price_payload(high=101.0, low=99.0, open_time_ms=_ms(signal_ts + timedelta(minutes=1))),
        )

        assert response.json()["closed"] == 0
        (position,) = _positions(client)
        assert position.status == "OPEN"

    def test_bougie_du_signal_pas_de_cloture(self, client):
        """Anti-lookahead : la position ouverte à la clôture de la bougie N ne
        doit jamais être vérifiée contre la bougie N elle-même."""
        signal_ts = datetime.now(timezone.utc)
        client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
        # open_time = 1 s AVANT le timestamp du signal (même bougie) : le low
        # touche pourtant le SL -> ne doit PAS clôturer.
        meme_bougie = _ms(signal_ts - timedelta(seconds=1))

        response = client.post(
            "/internal/prices",
            json=_price_payload(high=101.0, low=97.0, open_time_ms=meme_bougie),
        )

        assert response.json()["closed"] == 0
        (position,) = _positions(client)
        assert position.status == "OPEN"

    def test_symboles_independants(self, client):
        signal_ts = datetime.now(timezone.utc)
        client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
        # Bougie ETH très basse : ne doit pas clôturer la position BTC.
        client.post(
            "/internal/prices",
            json=_price_payload(
                symbol="ETHUSDT", high=50.0, low=10.0, open_time_ms=_ms(signal_ts + timedelta(minutes=1))
            ),
        )
        (position,) = _positions(client)
        assert position.status == "OPEN"


class TestNotificationCloture:
    def test_cloture_notifiee_dans_le_salon_recap(self, client):
        fake_recap = FakeNotifier()
        client.app.dependency_overrides[provide_recap_notifier] = lambda: fake_recap
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
            client.post(
                "/internal/prices",
                json=_price_payload(high=104.5, low=99.0, open_time_ms=_ms(signal_ts + timedelta(minutes=1))),
            )
        finally:
            client.app.dependency_overrides.pop(provide_recap_notifier, None)

        assert len(fake_recap.sent) == 1
        embed = fake_recap.sent[0]
        assert "Take Profit atteint" in embed.title
        assert "BTCUSDT" in embed.title
        # Jour + heure de l'ouverture et de la clôture, en heure de Paris.
        champs = {f.name: f.value for f in embed.fields}
        assert re.fullmatch(r"\w+ \d{2}/\d{2} \d{2}:\d{2}", champs["Ouvert le"])
        assert re.fullmatch(r"\w+ \d{2}/\d{2} \d{2}:\d{2}", champs["Clôturé le"])

    def test_cloture_par_signal_notifiee_aussi(self, client):
        """Voie historique : le prix d'entrée d'un nouveau signal clôture une
        position -> notification également publiée."""
        fake_recap = FakeNotifier()
        client.app.dependency_overrides[provide_recap_notifier] = lambda: fake_recap
        try:
            client.post("/webhook/tradingview", json=_signal_payload())
            suivant = _signal_payload(
                price="105", stop_loss="103", take_profit="108",
                timestamp=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
            client.post("/webhook/tradingview", json=suivant)
        finally:
            client.app.dependency_overrides.pop(provide_recap_notifier, None)

        assert len(fake_recap.sent) == 1
        assert "Take Profit atteint" in fake_recap.sent[0].title

    def test_echec_discord_ne_casse_pas_la_cloture(self, client):
        fake_recap = FakeNotifier()
        fake_recap.fail = True
        client.app.dependency_overrides[provide_recap_notifier] = lambda: fake_recap
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
            response = client.post(
                "/internal/prices",
                json=_price_payload(high=104.5, low=99.0, open_time_ms=_ms(signal_ts + timedelta(minutes=1))),
            )
        finally:
            client.app.dependency_overrides.pop(provide_recap_notifier, None)

        assert response.status_code == 200
        assert response.json()["closed"] == 1  # clôture en base malgré Discord KO
