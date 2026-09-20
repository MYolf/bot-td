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
    """Les clôtures sont routées : SL -> salon SL, TP -> salon TP (le salon
    récap ne reçoit plus que le récap hebdo)."""

    def _avec_tp_notifier(self, client):
        from app.services.discord_service import set_tp_notifier

        fake_tp = FakeNotifier()
        set_tp_notifier(fake_tp)
        return fake_tp

    def _nettoie(self):
        from app.services.discord_service import set_tp_notifier, set_sl_notifier

        set_tp_notifier(None)
        set_sl_notifier(None)

    def test_cloture_tp_notifiee_dans_le_salon_tp(self, client):
        fake_tp = self._avec_tp_notifier(client)
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
            response = client.post(
                "/internal/prices",
                json=_price_payload(high=104.5, low=99.0, open_time_ms=_ms(signal_ts + timedelta(minutes=1))),
            )
        finally:
            self._nettoie()

        assert response.json()["closed"] == 1
        # Bougie touchant TP2 (= TP du signal) : rappels TP1 + TP2 puis embed
        # de clôture, tous dans le salon TP.
        titres = [e.title for e in fake_tp.sent]
        assert "✅ TP1 validé — Trade #1" in titres
        assert "✅ TP2 validé — Trade #1" in titres
        assert any("Take Profit atteint" in t for t in titres)
        embed_cloture = fake_tp.sent[-1]
        assert "BTCUSDT" in embed_cloture.title
        # Jour + heure de l'ouverture et de la clôture, en heure de Paris.
        champs = {f.name: f.value for f in embed_cloture.fields}
        assert re.fullmatch(r"\w+ \d{2}/\d{2} \d{2}:\d{2}", champs["Ouvert le"])
        assert re.fullmatch(r"\w+ \d{2}/\d{2} \d{2}:\d{2}", champs["Clôturé le"])

    def test_cloture_sl_notifiee_dans_le_salon_sl(self, client):
        from app.services.discord_service import set_sl_notifier

        fake_sl = FakeNotifier()
        fake_tp = self._avec_tp_notifier(client)
        set_sl_notifier(fake_sl)
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
            response = client.post(
                "/internal/prices",
                json=_price_payload(high=101.0, low=97.5, open_time_ms=_ms(signal_ts + timedelta(minutes=1))),
            )
        finally:
            self._nettoie()

        assert response.json()["closed"] == 1
        assert len(fake_sl.sent) == 1
        assert "Stop Loss atteint" in fake_sl.sent[0].title
        assert len(fake_tp.sent) == 0  # rien côté TP

    def test_cloture_par_signal_notifiee_aussi(self, client):
        """Voie historique : le prix d'entrée d'un nouveau signal clôture une
        position -> notification routée dans le salon TP également."""
        fake_tp = self._avec_tp_notifier(client)
        try:
            client.post("/webhook/tradingview", json=_signal_payload())
            suivant = _signal_payload(
                price="105", stop_loss="103", take_profit="108",
                timestamp=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
            client.post("/webhook/tradingview", json=suivant)
        finally:
            self._nettoie()

        assert any("Take Profit atteint" in e.title for e in fake_tp.sent)

    def test_echec_discord_ne_casse_pas_la_cloture(self, client):
        fake_tp = self._avec_tp_notifier(client)
        fake_tp.fail = True
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
            response = client.post(
                "/internal/prices",
                json=_price_payload(high=104.5, low=99.0, open_time_ms=_ms(signal_ts + timedelta(minutes=1))),
            )
        finally:
            self._nettoie()

        assert response.status_code == 200
        assert response.json()["closed"] == 1  # clôture en base malgré Discord KO


class TestAlertesBreakEven:
    """Rappels BE (+1,5R) : BUY entry 100 / SL 98 -> déclencheur 103."""

    def test_be_atteint_notifie_une_seule_fois(self, client):
        from app.services.discord_service import provide_be_notifier

        fake_be = FakeNotifier()
        client.app.dependency_overrides[provide_be_notifier] = lambda: fake_be
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
            bougie = _ms(signal_ts + timedelta(minutes=1))
            # High 103.5 >= 103 (déclencheur), sans toucher TP 104 ni SL 98.
            client.post(
                "/internal/prices",
                json=_price_payload(high=103.5, low=99.0, open_time_ms=bougie),
            )
            # Le prix reste au-dessus à la bougie suivante (sans revenir à
            # l'entrée) : pas de spam, la position reste ouverte.
            client.post(
                "/internal/prices",
                json=_price_payload(high=103.9, low=100.5, open_time_ms=bougie + 90_000),
            )
        finally:
            client.app.dependency_overrides.pop(provide_be_notifier, None)

        assert len(fake_be.sent) == 1
        embed = fake_be.sent[0]
        assert embed.title == "🛡️ Break-even atteint — Trade #1"
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Déclencheur (+1,5R)"] == "103"
        assert champs["Action suggérée"] == "SL → entrée (100)"
        # La position reste ouverte : le paper trading garde son bracket.
        (position,) = _positions(client)
        assert position.status == "OPEN"
        assert position.be_notified is True

    def test_bougie_du_signal_pas_d_alerte(self, client):
        """Anti-lookahead : la bougie qui clôt le signal ne déclenche pas BE."""
        from app.services.discord_service import provide_be_notifier

        fake_be = FakeNotifier()
        client.app.dependency_overrides[provide_be_notifier] = lambda: fake_be
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
            meme_bougie = _ms(signal_ts - timedelta(seconds=1))

            response = client.post(
                "/internal/prices",
                json=_price_payload(high=110.0, low=99.0, open_time_ms=meme_bougie),
            )
        finally:
            client.app.dependency_overrides.pop(provide_be_notifier, None)

        assert response.status_code == 200
        assert len(fake_be.sent) == 0
        (position,) = _positions(client)
        assert position.be_notified is False

    def test_sl_et_be_meme_bougie_sl_prioritaire(self, client):
        """Low touche le SL et high le déclencheur BE : clôture SL, pas d'alerte."""
        from app.services.discord_service import provide_be_notifier

        fake_be = FakeNotifier()
        client.app.dependency_overrides[provide_be_notifier] = lambda: fake_be
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))

            response = client.post(
                "/internal/prices",
                json=_price_payload(
                    high=103.5, low=97.5, open_time_ms=_ms(signal_ts + timedelta(minutes=1))
                ),
            )
        finally:
            client.app.dependency_overrides.pop(provide_be_notifier, None)

        assert response.json()["closed"] == 1
        (trade,) = _trades(client)
        assert trade.exit_reason == "SL"
        assert len(fake_be.sent) == 0

    def test_sell_be_atteint_par_le_low(self, client):
        """SELL entry 100 / SL 102 -> déclencheur BE = 97 (risque 2 vers le bas)."""
        from app.services.discord_service import provide_be_notifier

        fake_be = FakeNotifier()
        client.app.dependency_overrides[provide_be_notifier] = lambda: fake_be
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post(
                "/webhook/tradingview",
                json=_signal_payload(
                    action="SELL", stop_loss="102", take_profit="96", timestamp=signal_ts
                ),
            )

            client.post(
                "/internal/prices",
                json=_price_payload(
                    high=101.0, low=96.8, open_time_ms=_ms(signal_ts + timedelta(minutes=1))
                ),
            )
        finally:
            client.app.dependency_overrides.pop(provide_be_notifier, None)

        assert len(fake_be.sent) == 1
        champs = {f.name: f.value for f in fake_be.sent[0].fields}
        assert champs["Déclencheur (+1,5R)"] == "97"

    def test_salon_non_configure_pas_d_erreur(self, client):
        """Sans override du notifieur BE (None par défaut) : détection quand même."""
        signal_ts = datetime.now(timezone.utc)
        client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))

        response = client.post(
            "/internal/prices",
            json=_price_payload(
                high=103.5, low=99.0, open_time_ms=_ms(signal_ts + timedelta(minutes=1))
            ),
        )

        assert response.status_code == 200
        (position,) = _positions(client)
        assert position.status == "OPEN"
        assert position.be_notified is True  # marqué, la notif est simplement ignorée


class TestSortiesPartielles:
    """TP1 (+1R) / TP2 (+2R) : BUY entry 100 / SL 98 -> TP1 = 102, TP2 = 104."""

    def _avec_tp_notifier(self, client):
        from app.services.discord_service import provide_tp_notifier, set_tp_notifier

        fake_tp = FakeNotifier()
        # Dépendance de route (rappels TP) ET global (routage des clôtures).
        client.app.dependency_overrides[provide_tp_notifier] = lambda: fake_tp
        set_tp_notifier(fake_tp)
        return fake_tp

    def test_tp1_valide_tp2_en_cours(self, client):
        fake_tp = self._avec_tp_notifier(client)
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
            bougie = _ms(signal_ts + timedelta(minutes=1))
            # High 102.5 touche TP1 (102), pas TP2 (104) ni le SL.
            client.post(
                "/internal/prices",
                json=_price_payload(high=102.5, low=99.0, open_time_ms=bougie),
            )
            # Bougie suivante toujours au-dessus de TP1 : pas de spam.
            client.post(
                "/internal/prices",
                json=_price_payload(high=103.0, low=99.0, open_time_ms=bougie + 90_000),
            )
        finally:
            from app.services.discord_service import provide_tp_notifier, set_tp_notifier

            client.app.dependency_overrides.pop(provide_tp_notifier, None)
            set_tp_notifier(None)

        assert len(fake_tp.sent) == 1
        embed = fake_tp.sent[0]
        assert embed.title == "✅ TP1 validé — Trade #1"
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Take Profits"] == (
            "TP1 : ✅ validé (102 · +2 pips)\nTP2 : ⏳ en cours (104 · +4 pips)"
        )
        # Position toujours ouverte, drapeaux persistés.
        (position,) = _positions(client)
        assert position.status == "OPEN"
        assert position.tp1_notified is True
        assert position.tp2_notified is False

    def test_tp2_valide_puis_cloture_tp(self, client):
        """Bougie touchant TP2 (= TP du signal) : rappels TP1 + TP2 puis
        clôture paper en TP."""
        fake_tp = self._avec_tp_notifier(client)
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
            response = client.post(
                "/internal/prices",
                json=_price_payload(
                    high=104.5, low=99.0, open_time_ms=_ms(signal_ts + timedelta(minutes=1))
                ),
            )
        finally:
            from app.services.discord_service import provide_tp_notifier, set_tp_notifier

            client.app.dependency_overrides.pop(provide_tp_notifier, None)
            set_tp_notifier(None)

        assert response.json()["closed"] == 1
        titres = [e.title for e in fake_tp.sent]
        assert titres[:2] == ["✅ TP1 validé — Trade #1", "✅ TP2 validé — Trade #1"]
        assert "Take Profit atteint" in titres[2]
        # Les deux niveaux affichés validés dans le rappel TP2.
        champs = {f.name: f.value for f in fake_tp.sent[1].fields}
        assert champs["Take Profits"] == (
            "TP1 : ✅ validé (102 · +2 pips)\nTP2 : ✅ validé (104 · +4 pips)"
        )

    def test_sl_et_tp1_meme_bougie_sl_prioritaire(self, client):
        """La bougie touche le SL et TP1 : clôture SL, aucun rappel TP."""
        fake_tp = self._avec_tp_notifier(client)
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
            response = client.post(
                "/internal/prices",
                json=_price_payload(
                    high=102.5, low=97.5, open_time_ms=_ms(signal_ts + timedelta(minutes=1))
                ),
            )
        finally:
            from app.services.discord_service import provide_tp_notifier, set_tp_notifier

            client.app.dependency_overrides.pop(provide_tp_notifier, None)
            set_tp_notifier(None)

        assert response.json()["closed"] == 1
        (trade,) = _trades(client)
        assert trade.exit_reason == "SL"
        assert len(fake_tp.sent) == 0

    def test_bougie_du_signal_pas_de_rappel(self, client):
        """Anti-lookahead : la bougie qui clôt le signal ne déclenche pas TP1."""
        fake_tp = self._avec_tp_notifier(client)
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
            meme_bougie = _ms(signal_ts - timedelta(seconds=1))
            client.post(
                "/internal/prices",
                json=_price_payload(high=110.0, low=99.0, open_time_ms=meme_bougie),
            )
        finally:
            from app.services.discord_service import provide_tp_notifier, set_tp_notifier

            client.app.dependency_overrides.pop(provide_tp_notifier, None)
            set_tp_notifier(None)

        assert len(fake_tp.sent) == 0
        (position,) = _positions(client)
        assert position.tp1_notified is False

    def test_sell_tp1_atteint_par_le_low(self, client):
        """SELL entry 100 / SL 102 -> TP1 = 98 (risque 2 vers le bas)."""
        fake_tp = self._avec_tp_notifier(client)
        try:
            signal_ts = datetime.now(timezone.utc)
            client.post(
                "/webhook/tradingview",
                json=_signal_payload(
                    action="SELL", stop_loss="102", take_profit="96", timestamp=signal_ts
                ),
            )
            client.post(
                "/internal/prices",
                json=_price_payload(
                    high=101.0, low=97.5, open_time_ms=_ms(signal_ts + timedelta(minutes=1))
                ),
            )
        finally:
            from app.services.discord_service import provide_tp_notifier, set_tp_notifier

            client.app.dependency_overrides.pop(provide_tp_notifier, None)
            set_tp_notifier(None)

        assert len(fake_tp.sent) == 1
        champs = {f.name: f.value for f in fake_tp.sent[0].fields}
        assert "TP1 : ✅ validé (98 · +2 pips)" in champs["Take Profits"]


class TestClotureBreakEven:
    """Clôture BE : position protégée au break-even (rappel +1,5R émis) puis
    prix revenu à l'entrée -> clôture à l'entrée, +0R, salon BE.

    BUY entry 100 / SL 98 / TP 104 : déclencheur BE = 103, entrée = 100.
    """

    def _ouvre_et_protege_be(self, client, *, action="BUY", stop_loss="98", take_profit="104"):
        """Signal + bougie qui déclenche le rappel BE (position reste ouverte)."""
        signal_ts = datetime.now(timezone.utc)
        client.post(
            "/webhook/tradingview",
            json=_signal_payload(
                action=action, stop_loss=stop_loss, take_profit=take_profit,
                timestamp=signal_ts,
            ),
        )
        bougie = _ms(signal_ts + timedelta(minutes=1))
        if action == "BUY":
            # High 103.5 >= 103 (BE), sans toucher l'entrée ni le TP.
            client.post(
                "/internal/prices",
                json=_price_payload(high=103.5, low=100.5, open_time_ms=bougie),
            )
        else:  # SELL SL 102 : BE = 97, touché par le low sans toucher l'entrée.
            client.post(
                "/internal/prices",
                json=_price_payload(high=99.5, low=96.8, open_time_ms=bougie),
            )
        (position,) = _positions(client)
        assert position.status == "OPEN"
        assert position.be_notified is True
        return signal_ts

    def test_retour_a_l_entree_cloture_be_0r(self, client):
        signal_ts = self._ouvre_et_protege_be(client)

        response = client.post(
            "/internal/prices",
            json=_price_payload(
                high=101.0, low=99.5,  # touche l'entrée 100, pas le SL 98
                open_time_ms=_ms(signal_ts + timedelta(minutes=2)),
            ),
        )

        assert response.json()["closed"] == 1
        (position,) = _positions(client)
        assert position.status == "CLOSED"
        assert position.result_r == Decimal("0")
        (trade,) = _trades(client)
        assert trade.exit_reason == "BE"
        assert trade.exit_price == Decimal("100")  # sortie au niveau de l'entrée

    def test_cloture_be_notifiee_dans_le_salon_be(self, client):
        from app.services.discord_service import (
            set_be_notifier,
            set_sl_notifier,
            set_tp_notifier,
        )

        fake_be = FakeNotifier()
        fake_sl = FakeNotifier()
        fake_tp = FakeNotifier()
        set_be_notifier(fake_be)
        set_sl_notifier(fake_sl)
        set_tp_notifier(fake_tp)
        try:
            signal_ts = self._ouvre_et_protege_be(client)
            response = client.post(
                "/internal/prices",
                json=_price_payload(
                    high=101.0, low=99.5,
                    open_time_ms=_ms(signal_ts + timedelta(minutes=2)),
                ),
            )
        finally:
            set_be_notifier(None)
            set_sl_notifier(None)
            set_tp_notifier(None)

        assert response.json()["closed"] == 1
        # Salon BE : le rappel +1,5R de la première bougie PUIS la clôture BE.
        titres_be = [e.title for e in fake_be.sent]
        assert titres_be == [
            "🛡️ Break-even atteint — Trade #1",
            "🛡️ Break-even touché — BTCUSDT",
        ]
        embed = fake_be.sent[-1]
        champs = {f.name: f.value for f in embed.fields}
        assert champs["Trade"] == "#1"
        assert champs["Résultat"] == "+0R"
        assert "TP1" in champs["Gestion"]
        assert len(fake_sl.sent) == 0
        # Côté TP : uniquement le rappel TP1 de la première bougie, rien d'autre.
        titres_tp = [e.title for e in fake_tp.sent]
        assert titres_tp == ["✅ TP1 validé — Trade #1"]

    def test_sl_origine_touche_apres_be_toujours_be(self, client):
        """Bougie qui traverse le SL d'ORIGINE après le BE : le stop actif est
        l'entrée, la clôture reste BE à 0R (jamais -1R)."""
        signal_ts = self._ouvre_et_protege_be(client)

        response = client.post(
            "/internal/prices",
            json=_price_payload(
                high=101.0, low=97.5,  # sous le SL d'origine 98
                open_time_ms=_ms(signal_ts + timedelta(minutes=2)),
            ),
        )

        assert response.json()["closed"] == 1
        (trade,) = _trades(client)
        assert trade.exit_reason == "BE"
        assert trade.exit_price == Decimal("100")
        (position,) = _positions(client)
        assert position.result_r == Decimal("0")

    def test_be_actif_tp_atteint_sans_l_entree_cloture_tp(self, client):
        signal_ts = self._ouvre_et_protege_be(client)

        response = client.post(
            "/internal/prices",
            json=_price_payload(
                high=104.5, low=101.0,  # touche le TP, jamais l'entrée
                open_time_ms=_ms(signal_ts + timedelta(minutes=2)),
            ),
        )

        assert response.json()["closed"] == 1
        (trade,) = _trades(client)
        assert trade.exit_reason == "TP"
        (position,) = _positions(client)
        assert position.result_r == Decimal("2")

    def test_sell_retour_a_l_entree_cloture_be(self, client):
        """SELL entry 100 / SL 102 : le high revient toucher l'entrée."""
        signal_ts = self._ouvre_et_protege_be(
            client, action="SELL", stop_loss="102", take_profit="96"
        )

        response = client.post(
            "/internal/prices",
            json=_price_payload(
                high=100.5, low=99.0,  # touche l'entrée 100, pas le SL 102
                open_time_ms=_ms(signal_ts + timedelta(minutes=2)),
            ),
        )

        assert response.json()["closed"] == 1
        (trade,) = _trades(client)
        assert trade.exit_reason == "BE"
        assert trade.exit_price == Decimal("100")
        (position,) = _positions(client)
        assert position.result_r == Decimal("0")

    def test_bougie_du_be_pas_de_cloture_sur_cette_bougie(self, client):
        """La bougie qui DÉCLENche le rappel BE ne clôture pas : le drapeau
        n'est positionné qu'après check_candle (le low ne touche pas l'entrée
        ici, mais la bougie BE est traitée avec le bracket d'origine)."""
        signal_ts = datetime.now(timezone.utc)
        client.post("/webhook/tradingview", json=_signal_payload(timestamp=signal_ts))
        # High 103.5 (BE) et low 99.5 : l'entrée n'est PAS touchée, position
        # ouverte, BE marqué -> c'est la bougie SUIVANTE qui pourra clôturer.
        client.post(
            "/internal/prices",
            json=_price_payload(
                high=103.5, low=99.5, open_time_ms=_ms(signal_ts + timedelta(minutes=1))
            ),
        )
        (position,) = _positions(client)
        assert position.status == "OPEN"
        assert position.be_notified is True
        assert _trades(client) == []

    def test_cloture_be_par_prix_d_un_nouveau_signal(self, client):
        """Voie webhook : le prix d'entrée d'un nouveau signal sous l'entrée
        (mais au-dessus du SL d'origine) clôture la position protégée en BE."""
        signal_ts = self._ouvre_et_protege_be(client)

        suivant = _signal_payload(
            price="99.5", stop_loss="97.5", take_profit="103.5",
            timestamp=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        client.post("/webhook/tradingview", json=suivant)

        trades = _trades(client)
        assert len(trades) == 1
        assert trades[0].exit_reason == "BE"
        assert trades[0].exit_price == Decimal("100")
        positions = _positions(client)
        assert positions[0].status == "CLOSED"
        assert positions[0].result_r == Decimal("0")
        assert positions[1].status == "OPEN"  # la nouvelle position
