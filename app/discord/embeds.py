"""Embeds Discord des signaux (Phase 12).

Fonctions pures : constructibles et testables sans connexion Discord.
Format conforme à Projet.md §22, lisible sur mobile : titre normalisé
(🟢 LONG SIGNAL / 🔴 SHORT SIGNAL), champs nommés, prix lisibles, heure UTC.
"""

from datetime import datetime, timezone
from decimal import Decimal

import discord

from app.paper_trading.statistics import PerformanceStats

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
    score: int | None = None,
) -> discord.Embed:
    """Construit l'embed d'un signal validé (BUY/SELL).

    `score` (Phase 26) : qualité interne du signal sur 100, affichée
    uniquement si la stratégie en envoie les composantes.
    """
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
    if score is not None:
        embed.add_field(name="Signal Score", value=f"{score}/100", inline=True)
        # Projet.md §40 : jamais présenté comme une probabilité de gain.
        embed.set_footer(text="Score = qualité interne du signal (pas une probabilité de gain)")
    return embed


# --- Phase 20 : embeds des commandes slash ---

BLUE = 0x3498DB  # listes
PURPLE = 0x9B59B6  # statistiques
GREY = 0x95A5A6  # stratégies


def build_signal_embed_from_row(signal, strategy_name: str) -> discord.Embed:
    """Embed d'un signal chargé depuis la base (commandes /lastsignal, /signals).

    Le paramètre `signal` est un `app.database.models.Signal` (typage en str
    pour éviter une dépendance circulaire embeds -> models).
    """
    return build_signal_embed(
        action=signal.action,
        symbol=signal.symbol,
        strategy=strategy_name,
        timeframe=signal.timeframe,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        risk_reward=signal.risk_reward,
        signal_time=signal.signal_timestamp,
        score=signal.score,
    )


def build_signals_list_embed(rows: list) -> discord.Embed:
    """Liste compacte des derniers signaux (commande /signals).

    `rows` : liste de tuples (Signal, nom de stratégie) fournie par
    `SignalRepository.get_latest`.
    """
    embed = discord.Embed(title="📋 Derniers signaux", color=BLUE)
    lines = []
    for signal, strategy_name in rows:
        emoji = "🟢" if signal.action == "BUY" else "🔴"
        moment = _as_utc(signal.signal_timestamp).strftime("%d/%m %H:%M UTC")
        lines.append(
            f"{emoji} **{signal.symbol}** · {strategy_label(strategy_name)} "
            f"· {timeframe_label(signal.timeframe)} · {moment} · {signal.status}"
        )
    embed.description = "\n".join(lines)
    return embed


def build_stats_embed(
    *,
    total: int,
    par_statut: dict[str, int],
    par_action: dict[str, int],
    par_strategie: dict[str, int],
    paper: "PerformanceStats",
    ouvertes: int = 0,
    filtre: str | None = None,
) -> discord.Embed:
    """Statistiques des signaux stockés + paper trading (commande /stats).

    Les performances sont en R-multiples (simulation locale, Phase 21) :
    toujours mentionner le nombre de trades — un échantillon < 30 n'a aucune
    signification statistique. `filtre` (Phases 23-24) affiche les critères
    de filtrage actifs.
    """
    embed = discord.Embed(title="📊 Statistiques des signaux", color=PURPLE)
    if filtre:
        embed.description = f"Filtre : {filtre}"
    embed.add_field(name="Total", value=str(total), inline=True)
    embed.add_field(
        name="Par action",
        value="\n".join(f"{k}: {v}" for k, v in sorted(par_action.items())) or "—",
        inline=True,
    )
    embed.add_field(
        name="Par statut",
        value="\n".join(f"{k}: {v}" for k, v in sorted(par_statut.items())) or "—",
        inline=True,
    )
    embed.add_field(
        name="Par stratégie",
        value="\n".join(
            f"{strategy_label(k)}: {v}" for k, v in sorted(par_strategie.items())
        )
        or "—",
        inline=False,
    )
    if paper.total > 0:
        papier = (
            f"{paper.total} clôturées · {ouvertes} ouvertes\n"
            f"Win rate : {paper.win_rate}% ({paper.wins}W / {paper.losses}L)\n"
            f"Total : {paper.total_r} R · Moyenne : {paper.avg_r} R\n"
            f"Max drawdown : {paper.max_drawdown_r} R"
        )
    else:
        papier = f"Aucune position clôturée ({ouvertes} ouverte(s))"
    embed.add_field(name="Paper trading (R)", value=papier, inline=False)
    embed.set_footer(text="Simulation locale en R — moins de 30 trades = non significatif")
    return embed


def build_strategies_embed(strategies: list, compte: dict[str, int]) -> discord.Embed:
    """Stratégies enregistrées (commande /strategy).

    `strategies` : liste de `app.database.models.Strategy` ; `compte` :
    effectifs de signaux par nom de stratégie.
    """
    embed = discord.Embed(title="🧭 Stratégies", color=GREY)
    if not strategies:
        embed.description = "Aucune stratégie enregistrée."
        return embed
    for strategy in strategies:
        etat = "✅ active" if strategy.enabled else "⛔ désactivée"
        embed.add_field(
            name=strategy_label(strategy.name),
            value=f"{etat}\nVersion : {strategy.version}\nSignaux : {compte.get(strategy.name, 0)}",
            inline=True,
        )
    return embed
