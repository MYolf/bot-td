"""Indicateurs techniques — portage Python fidèle des ta.* de Pine Script.

Conventions identiques à TradingView :
- ``ta.ema`` : EMA classique, amorcée par la SMA des ``length`` premières
  valeurs (première valeur définie à l'index ``length - 1``) ;
- ``ta.rsi`` : RSI de Wilder (RMA des gains/pertes), amorce SMA ;
- ``ta.macd`` : ligne = EMA rapide − EMA lente, signal = EMA de la ligne.

Chaque fonction retourne une liste de même longueur que l'entrée, avec
``None`` pendant la période d'amorce (équivalent de ``na`` en Pine).
"""

from __future__ import annotations


def sma(values: list[float], length: int) -> list[float | None]:
    """Moyenne mobile simple (fenêtre glissante)."""
    out: list[float | None] = [None] * len(values)
    if length <= 0 or len(values) < length:
        return out
    window = sum(values[:length])
    out[length - 1] = window / length
    for i in range(length, len(values)):
        window += values[i] - values[i - length]
        out[i] = window / length
    return out


def ema(values: list[float], length: int) -> list[float | None]:
    """Moyenne mobile exponentielle, amorce SMA (comme ta.ema)."""
    out: list[float | None] = [None] * len(values)
    if length <= 0 or len(values) < length:
        return out
    prev = sum(values[:length]) / length
    out[length - 1] = prev
    alpha = 2.0 / (length + 1)
    for i in range(length, len(values)):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def rma(values: list[float], length: int) -> list[float | None]:
    """Moyenne mobile de Wilder (comme ta.rma), amorce SMA."""
    out: list[float | None] = [None] * len(values)
    if length <= 0 or len(values) < length:
        return out
    prev = sum(values[:length]) / length
    out[length - 1] = prev
    for i in range(length, len(values)):
        prev = (prev * (length - 1) + values[i]) / length
        out[i] = prev
    return out


def rsi(closes: list[float], length: int) -> list[float | None]:
    """RSI de Wilder (comme ta.rsi : rma des gains et des pertes)."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < length + 1:
        return out
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, n):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = rma(gains, length)
    avg_loss = rma(losses, length)
    # avg_*[k] est aligné sur closes[k + 1].
    for k in range(length - 1, len(gains)):
        gain = avg_gain[k]
        loss = avg_loss[k]
        if gain is None or loss is None:
            continue
        denom = gain + loss
        if denom == 0.0:
            continue  # bougies strictement plates : na en Pine aussi
        out[k + 1] = 100.0 * gain / denom
    return out


def true_range(
    highs: list[float], lows: list[float], closes: list[float]
) -> list[float | None]:
    """True Range (comme ta.tr). Non défini sur la première bougie."""
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(1, n):
        out[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return out


def atr(
    highs: list[float], lows: list[float], closes: list[float], length: int
) -> list[float | None]:
    """ATR (comme ta.atr) : RMA du true range.

    Le true range est défini à partir de l'index 1 ; l'ATR l'est donc à
    partir de l'index ``length`` (amorce RMA de ``length`` valeurs).
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    tr = true_range(highs, lows, closes)
    compact = [v for v in tr if v is not None]  # tr[i] défini pour i >= 1
    smoothed = rma(compact, length)
    for k, value in enumerate(smoothed):
        if value is not None:
            out[k + 1] = value
    return out


def macd(
    closes: list[float], fast: int, slow: int, signal_len: int
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """MACD (comme ta.macd) : (ligne, signal, histogramme)."""
    n = len(closes)
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    line: list[float | None] = [None] * n
    compact: list[float] = []  # valeurs de la ligne, sans les None d'amorce
    for i in range(n):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            line[i] = ema_fast[i] - ema_slow[i]  # type: ignore[operator]
            compact.append(line[i])  # type: ignore[arg-type]
    signal_compact = ema(compact, signal_len)
    signal: list[float | None] = [None] * n
    start = slow - 1  # index de la première valeur définie de la ligne
    for k, value in enumerate(signal_compact):
        if value is not None:
            signal[start + k] = value
    hist: list[float | None] = [None] * n
    for i in range(n):
        if line[i] is not None and signal[i] is not None:
            hist[i] = line[i] - signal[i]
    return line, signal, hist
