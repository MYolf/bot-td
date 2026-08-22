"""Embeds Discord des signaux (Phase 12).

Fonctions pures : constructibles et testables sans connexion Discord.
Format conforme à Projet.md §22, lisible sur mobile : titre normalisé
(🟢 LONG SIGNAL / 🔴 SHORT SIGNAL), champs nommés, prix lisibles, heure UTC.
"""

from datetime import datetime, timezone
from decimal import Decimal

import discord

GREEN = 0x2ECC71  # LONG
RED = 0xE74C3C  # SHORT

# Correspondance timeframe interne -> affichage
_TIMEFRAME_LABELS = {"60": "1h", "240": "4h", "D": "1D"}


def timeframe_label(timeframe: str) -> str:
    """Rend le timeframe lisible ("15" -> "15m")."""
    if timeframe in _TIMEFRAME_LABELS:
        return _TIMEFRAME_LABELS[timeframe]
    return f"{timeframe}m"


def strategy_label(strategy: str) -> str:
    """Rend la stratégie lisible ("momentum_v1" -> "Momentum V1")."""
    return strategy.replace("_", " ").title()


def format_price(value: Decimal | float | str) -> str:
    """Prix lisible : séparateurs de milliers, 2 décimales maximum."""
    decimal_value = Decimal(str(value))
    formatted = f"{decimal_value:,.2f}"
    if formatted.endswith(".00"):
        formatted = formatted[:-3]
    return formatted


def format_risk_reward(rr: Decimal | float | str) -> str:
    """RR décimal -> ratio lisible ("1:2", "1:1.5")."""
    value = Decimal(str(rr)).quantize(Decimal("0.01")).normalize()
    text = format(value, "f")
    return f"1:{text}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_signal_embed(
    *,
    action: str,
    symbol: str,
    strategy: str,
    timeframe: str,
    entry_price: Decimal | float | str,
    stop_loss: Decimal | float | str,
    take_profit: Decimal | float | str,
    risk_reward: Decimal | float | str,
    signal_time: datetime,
) -> discord.Embed:
    """Construit l'embed d'un signal validé (BUY/SELL)."""
    is_buy = action == "BUY"
    embed = discord.Embed(
        title=f"{'🟢 LONG SIGNAL' if is_buy else '🔴 SHORT SIGNAL'} — {symbol}",
        color=GREEN if is_buy else RED,
    )
    embed.add_field(name="Strategy", value=strategy_label(strategy), inline=True)
    embed.add_field(name="Timeframe", value=timeframe_label(timeframe), inline=True)
    embed.add_field(name="Entry", value=format_price(entry_price), inline=True)
    embed.add_field(name="Stop Loss", value=format_price(stop_loss), inline=True)
    embed.add_field(name="Take Profit", value=format_price(take_profit), inline=True)
    embed.add_field(
        name="Risk/Reward", value=format_risk_reward(risk_reward), inline=True
    )
    embed.add_field(
        name="Signal Time",
        value=_as_utc(signal_time).strftime("%H:%M:%S UTC"),
        inline=True,
    )
    return embed
