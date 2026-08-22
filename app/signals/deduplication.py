"""Déduplication des signaux (Phase 10).

Identifiant unique d'un signal : strategy:symbol:timeframe:timestamp_bougie:action
Le timestamp est arrondi à la seconde (temps de bougie transmis par TradingView).

Deux envois identiques de TradingView produisent le même signal_uid : la
contrainte UNIQUE en base garantit qu'un seul sera stocké, donc un seul
message Discord (Phase 12).
"""

from datetime import datetime, timezone


def build_signal_uid(
    strategy: str,
    symbol: str,
    timeframe: str,
    candle_timestamp: datetime,
    action: str,
) -> str:
    """Construit le signal_uid (ex. momentum_v1:BTCUSDT:15:1755892800:BUY)."""
    ts = candle_timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    unix_seconds = int(ts.timestamp())
    return f"{strategy}:{symbol}:{timeframe}:{unix_seconds}:{action}"
