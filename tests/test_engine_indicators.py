"""Tests des indicateurs du moteur (engine/indicators.py).

Valeurs attendues calculées à la main — amorce SMA puis récurrence,
fidèle aux ta.* de Pine Script.
"""

from engine.indicators import ema, macd, rma, rsi, sma


def test_sma_fenetre_glissante() -> None:
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_sma_historique_insuffisant() -> None:
    assert sma([1, 2], 3) == [None, None]


def test_ema_amorce_sma_puis_recurrence() -> None:
    # amorce : SMA(1,2,3) = 2 ; alpha = 2/4 = 0.5
    # ema[3] = 0.5*4 + 0.5*2 = 3 ; ema[4] = 0.5*5 + 0.5*3 = 4
    assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_rma_amorce_sma_puis_recurrence_wilder() -> None:
    # amorce : SMA(1,1,1,1) = 1 ; rma[4] = (1*3 + 10) / 4 = 3.25
    result = rma([1, 1, 1, 1, 10], 4)
    assert result[:4] == [None, None, None, 1.0]
    assert abs(result[4] - 3.25) < 1e-12


def test_rsi_hausse_monotone_egale_100() -> None:
    closes = [float(i) for i in range(30)]
    values = rsi(closes, 14)
    assert values[-1] == 100.0  # uniquement des gains


def test_rsi_baisse_monotone_egale_0() -> None:
    closes = [float(30 - i) for i in range(30)]
    values = rsi(closes, 14)
    assert values[-1] == 0.0  # uniquement des pertes


def test_rsi_serie_plate_non_definie() -> None:
    closes = [100.0] * 30  # gains = pertes = 0 -> na en Pine
    assert rsi(closes, 14)[-1] is None


def test_rsi_bornes() -> None:
    # Série alternée : le RSI doit rester dans (0, 100).
    closes = [100.0 + (i % 2) * 3 for i in range(60)]
    values = [v for v in rsi(closes, 14) if v is not None]
    assert values and all(0.0 < v < 100.0 for v in values)


def test_macd_coherence_avec_ema() -> None:
    closes = [100.0 + (i % 7) * 2 - 5 for i in range(60)]
    line, signal, hist = macd(closes, 12, 26, 9)
    ema_fast = ema(closes, 12)
    ema_slow = ema(closes, 26)
    for i in range(len(closes)):
        if line[i] is not None:
            assert ema_fast[i] is not None and ema_slow[i] is not None
            assert abs(line[i] - (ema_fast[i] - ema_slow[i])) < 1e-9  # type: ignore[operator]
        else:
            # ligne définie dès que les deux EMA le sont (index slow-1)
            assert i < 25
        if hist[i] is not None:
            assert abs(hist[i] - (line[i] - signal[i])) < 1e-9  # type: ignore[operator]
    # signal défini après ligne + amorce
    first_signal = next(i for i, v in enumerate(signal) if v is not None)
    assert first_signal == 25 + 9 - 1  # (slow-1) + (signal_len-1)
