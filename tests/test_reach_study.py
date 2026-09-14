"""Tests de la reach study (engine/reach_study.py, spec ANTICIPATION.md).

Points critiques testés :
- la sémantique des issues par horizon (spec §3) : CONFIRMED / CANCELLED /
  EXPIRED / INVALIDATED / SIGNAL_NO_FILL ;
- l'anti-lookahead : la distance (et le niveau) d'un candidat à la bougie i
  ne dépend QUE de l'open de i et de l'état des bougies fermées ;
- les buckets scellés et la règle anti-spam du volume ;
- le découpage IS/OOS scellé (spec §5).
"""

import random

from engine.indicators import atr
from engine.reach_study import (
    BUCKETS,
    CANCELLED,
    CONFIRMED,
    EXPIRED,
    INVALIDATED,
    IS_DAYS,
    SIGNAL_NO_FILL,
    ReachCandidate,
    _bucket_for,
    _resolve_outcome,
    announce_volume,
    run_reach_study,
    split_stage,
)
from engine.strategy import Candle, MomentumParams

STEP_MS = 900_000
DAY_MS = 86_400_000


def _random_walk_candles(n: int, seed: int = 42) -> list[Candle]:
    """Marche aléatoire en régimes alternés (comme test_touch_study)."""
    rng = random.Random(seed)
    candles: list[Candle] = []
    price = 100.0
    for i in range(n):
        regime = 1.0 if (i // 80) % 2 == 0 else -1.0
        price *= 1 + regime * 0.003 + rng.gauss(0, 0.004)
        high = price * (1 + abs(rng.gauss(0, 0.001)))
        low = price * (1 - abs(rng.gauss(0, 0.001)))
        candles.append(
            Candle(
                open_time=i * STEP_MS,
                close_time=i * STEP_MS + STEP_MS - 1,
                open=price,
                high=max(high, price, low),
                low=min(low, price, high),
                close=price,
                volume=1.0,
            )
        )
    return candles


def _candle(i: int, open_: float, high: float, low: float, close: float) -> Candle:
    """Bougie à la POSITION i de la liste (les index de _resolve_outcome
    sont des positions, pas des timestamps)."""
    return Candle(
        open_time=i * STEP_MS,
        close_time=i * STEP_MS + STEP_MS - 1,
        open=open_,
        high=max(open_, high, close, low),
        low=min(open_, low, close, high),
        close=close,
        volume=1.0,
    )


def _padded(index: int, *candles: Candle) -> list[Candle]:
    """Liste où la bougie cible est à la position ``index`` (amorce neutre
    avant, flat après pour que les horizons ne débordent pas)."""
    out: list[Candle] = []
    for i in range(index):
        out.append(_candle(i, 100.0, 100.0, 100.0, 100.0))
    out.extend(candles)
    while len(out) < index + 8:
        i = len(out)
        out.append(_candle(i, 100.0, 100.0, 100.0, 100.0))
    return out


def _candidate(index: int, action: str, level: float, **kwargs) -> ReachCandidate:
    defaults = dict(
        symbol="TEST",
        open_time=index * STEP_MS,
        level=level,
        d_atr=0.5,
        bucket=0.5,
        fillable_pre=True,
    )
    defaults.update(kwargs)
    return ReachCandidate(index=index, action=action, **defaults)


class TestBucket:
    def test_frontières_scellées(self):
        assert _bucket_for(0.10) == 0.25
        assert _bucket_for(0.25) == 0.25
        assert _bucket_for(0.26) == 0.5
        assert _bucket_for(1.0) == 1.0
        assert _bucket_for(1.4) == 1.5
        assert _bucket_for(2.0) == 2.0
        assert _bucket_for(2.01) is None
        assert BUCKETS == (0.25, 0.5, 1.0, 1.5, 2.0)


class TestResolveOutcome:
    """Sémantique exacte du cycle de vie runtime (ANTICIPATION.md §3)."""

    def setup_method(self):
        self.params = MomentumParams()

    def test_touché_confirmé(self):
        # bougie 5 touche 105 (BUY) et le moteur émet à sa clôture
        candles = _padded(5, _candle(5, 100.0, 105.0, 99.0, 104.0))
        cand = _candidate(5, "BUY", 105.0)
        _resolve_outcome(cand, candles, {5: {"BUY"}}, 1, self.params)
        assert cand.outcomes[1] == CONFIRMED
        assert cand.fill_advantage[1] == (104.0 - 105.0) / 105.0
        assert cand.resolve_idx[1] == 5

    def test_touché_non_confirmé_annulation(self):
        # touche mais pas d'émission à la clôture de la bougie touchante
        candles = _padded(5, _candle(5, 100.0, 105.5, 99.0, 101.0))
        cand = _candidate(5, "BUY", 105.0)
        _resolve_outcome(cand, candles, {}, 1, self.params)
        assert cand.outcomes[1] == CANCELLED
        assert cand.bail_out[1] == (101.0 - 105.0) / 105.0

    def test_sell_touché_annulé_signé(self):
        candles = _padded(5, _candle(5, 100.0, 101.0, 94.0, 97.0))
        cand = _candidate(5, "SELL", 95.0)
        _resolve_outcome(cand, candles, {}, 1, self.params)
        assert cand.outcomes[1] == CANCELLED
        # SELL : avantage = (level − close)/level, négatif si clôture SOUS le niveau
        assert cand.bail_out[1] == (95.0 - 97.0) / 95.0

    def test_jamais_touché_expiré(self):
        candles = _padded(
            5,
            _candle(5, 100.0, 101.0, 99.0, 100.5),
            _candle(6, 100.5, 101.5, 99.5, 101.0),
            _candle(7, 101.0, 102.0, 100.0, 101.5),
        )
        cand = _candidate(5, "BUY", 105.0)
        _resolve_outcome(cand, candles, {}, 3, self.params)
        assert cand.outcomes[3] == EXPIRED
        assert cand.resolve_idx[3] == 7

    def test_signal_opposé_invalide(self):
        # pas de touche, mais le moteur émet SELL pendant la validité
        candles = _padded(
            5,
            _candle(5, 100.0, 101.0, 99.0, 100.5),
            _candle(6, 100.5, 100.8, 98.0, 98.5),
        )
        cand = _candidate(5, "BUY", 105.0)
        _resolve_outcome(cand, candles, {6: {"SELL"}}, 2, self.params)
        assert cand.outcomes[2] == INVALIDATED
        assert cand.resolve_idx[2] == 6

    def test_signal_même_sens_sans_touche(self):
        # signal officiel sans que le niveau limite soit touché
        candles = _padded(
            5,
            _candle(5, 100.0, 101.0, 99.0, 100.5),
            _candle(6, 100.5, 101.5, 99.5, 101.0),
        )
        cand = _candidate(5, "BUY", 105.0)
        _resolve_outcome(cand, candles, {6: {"BUY"}}, 2, self.params)
        assert cand.outcomes[2] == SIGNAL_NO_FILL

    def test_touche_tardive_dans_lhorizon(self):
        # horizon 2 : la touche à la bougie 6 compte, confirmation aussi
        candles = _padded(
            5,
            _candle(5, 100.0, 101.0, 99.0, 100.5),
            _candle(6, 100.5, 106.0, 99.5, 105.5),
        )
        cand = _candidate(5, "BUY", 105.0)
        _resolve_outcome(cand, candles, {6: {"BUY"}}, 2, self.params)
        assert cand.outcomes[2] == CONFIRMED
        # même situation, horizon 1 : expiré sans touche
        cand2 = _candidate(5, "BUY", 105.0)
        _resolve_outcome(cand2, candles, {6: {"BUY"}}, 1, self.params)
        assert cand2.outcomes[1] == EXPIRED

    def test_horizon_tronqué_en_fin_dhistorique(self):
        # une seule bougie : l'horizon 8 est tronqué, pas de touche -> expiré
        candles = _padded(5, _candle(5, 100.0, 101.0, 99.0, 100.5))
        candles = candles[:6]  # rien après la position 5
        cand = _candidate(5, "BUY", 105.0)
        _resolve_outcome(cand, candles, {}, 8, self.params)
        assert cand.outcomes[8] == EXPIRED


class TestAntiLookahead:
    def test_candidat_i_indépendant_du_corps_de_la_bougie_i(self):
        """d_atr et le niveau du candidat i ne bougent pas si on change le
        high/low/close de la bougie i (seul l'open est connu à l'annonce)."""
        base = _random_walk_candles(1500, seed=3)
        params = MomentumParams()
        ref = run_reach_study("TEST", base, params, min_score=0)
        assert ref, "le jeu synthétique doit produire des candidats"
        # mutation : bougie 800 amputée de son range, open inchangé
        mutated = list(base)
        c = mutated[800]
        mutated[800] = Candle(
            open_time=c.open_time,
            close_time=c.close_time,
            open=c.open,
            high=c.open,
            low=c.open,
            close=c.open,
            volume=c.volume,
        )
        alt = run_reach_study("TEST", mutated, params, min_score=0)
        ref_i = {x.action: x for x in ref if x.index == 800}
        alt_i = {x.action: x for x in alt if x.index == 800}
        assert set(ref_i) == set(alt_i)
        for action in ref_i:
            assert ref_i[action].level == alt_i[action].level
            assert ref_i[action].d_atr == alt_i[action].d_atr

    def test_d_atr_egal_distance_surverticale_atr_fermée(self):
        """d = |P* − open_i| / ATR14[i−1] exactement."""
        candles = _random_walk_candles(1500, seed=5)
        params = MomentumParams()
        cands = run_reach_study("TEST", candles, params, min_score=0)
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        closes = [c.close for c in candles]
        atr14 = atr(highs, lows, closes, 14)
        for cand in cands[:200]:
            a = atr14[cand.index - 1]
            expected = abs(cand.level - candles[cand.index].open) / a
            assert abs(cand.d_atr - expected) < 1e-12
            assert cand.bucket == _bucket_for(cand.d_atr)


class TestRunReachStudy:
    def test_end_to_end_cohérence(self):
        candles = _random_walk_candles(1800, seed=11)
        params = MomentumParams()
        cands = run_reach_study("TEST", candles, params, min_score=0)
        assert cands, "le jeu synthétique doit produire des candidats"
        valid = {CONFIRMED, CANCELLED, INVALIDATED, SIGNAL_NO_FILL, EXPIRED}
        for cand in cands:
            assert cand.fillable_pre is True or cand.fillable_pre is False
            assert set(cand.outcomes) == {1, 2, 4, 8}
            for horizon, outcome in cand.outcomes.items():
                assert outcome in valid
                assert cand.resolve_idx[horizon] >= cand.index
            if cand.outcomes[1] == CONFIRMED:
                assert cand.fill_advantage[1] is not None
            if cand.outcomes[1] == CANCELLED:
                assert cand.bail_out[1] is not None

    def test_filtrer_par_score_réduit_les_candidats(self):
        candles = _random_walk_candles(1800, seed=13)
        params = MomentumParams()
        sans_filtre = run_reach_study("TEST", candles, params, min_score=0)
        filtre = run_reach_study("TEST", candles, params, min_score=45)
        assert len(filtre) < len(sans_filtre)
        # tout candidat filtré est un sous-ensemble exact (mêmes index/actions)
        clefs = {(c.index, c.action) for c in filtre}
        toutes = {(c.index, c.action) for c in sans_filtre}
        assert clefs <= toutes


class TestAnnounceVolume:
    def test_anti_spam_une_active_par_direction(self):
        # BUY résolu à 11, BUY suivant à 11 -> SAUTÉ ; BUY à 12 -> compté ;
        # SELL à 11 -> compté (direction différente)
        cands = [
            _candidate(10, "BUY", 105.0),
            _candidate(11, "BUY", 105.2),
            _candidate(12, "BUY", 104.8),
            _candidate(11, "SELL", 95.0),
        ]
        cands[0].resolve_idx[2] = 11
        cands[1].resolve_idx[2] = 12
        cands[2].resolve_idx[2] = 13
        cands[3].resolve_idx[2] = 12
        for c in cands:
            c.d_atr = 0.4  # sous le seuil cumulatif 0.5
        assert announce_volume(cands, 0.5, 2, days=1.0) == 3.0

    def test_normalisation_par_jours(self):
        c = _candidate(10, "BUY", 105.0)
        c.d_atr = 0.4
        c.resolve_idx[1] = 10
        assert announce_volume([c], 0.5, 1, days=10.0) == 0.1

    def test_seuil_cumulatif(self):
        """Le bucket est un seuil : d = 0.3 compte sous k = 0.5."""
        c = _candidate(10, "BUY", 105.0)
        c.d_atr = 0.3  # hors bande 0.25 mais sous le seuil 0.5
        c.resolve_idx[1] = 10
        assert announce_volume([c], 0.5, 1, days=1.0) == 1.0
        assert announce_volume([c], 0.25, 1, days=1.0) == 0.0

    def test_non_fillable_ignoré(self):
        c = _candidate(10, "BUY", 105.0, fillable_pre=False)
        c.resolve_idx[1] = 10
        assert announce_volume([c], 0.5, 1, days=1.0) == 0.0


class TestSplitStage:
    def test_découpage_scellé_is_puis_oos(self):
        # une bougie par jour pendant 1490 j : IS = jours 0..1094, OOS = suite
        candles = []
        for d in range(1490):
            candles.append(
                Candle(
                    open_time=d * DAY_MS,
                    close_time=d * DAY_MS + DAY_MS - 1,
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.0,
                    volume=1.0,
                )
            )
        is_part, is_days = split_stage(candles, "is")
        oos_part, oos_days = split_stage(candles, "oos")
        assert len(is_part) == IS_DAYS
        assert len(oos_part) == 1490 - IS_DAYS
        assert is_part[-1].open_time < candles[0].open_time + IS_DAYS * DAY_MS
        assert oos_part[0].open_time >= candles[0].open_time + IS_DAYS * DAY_MS
        assert abs(is_days - IS_DAYS) < 1.0
        # spec §5 : OOS = jours 1096->1490 (395 jours sur 1490 chargés)
        assert abs(oos_days - 395.0) < 1.0

    def test_historique_trop_court(self):
        import pytest

        candles = [_candle(i, 100.0, 101.0, 99.0, 100.0) for i in range(10)]
        with pytest.raises(ValueError):
            split_stage(candles, "oos")
