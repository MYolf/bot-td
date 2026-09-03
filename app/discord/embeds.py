"""Embeds Discord des signaux (Phase 12).

Fonctions pures : constructibles et testables sans connexion Discord.
Format conforme à Projet.md §22, lisible sur mobile : titre normalisé
(🟢 LONG SIGNAL / 🔴 SHORT SIGNAL), champs nommés, prix lisibles, heure UTC.
"""

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import discord

from app.paper_trading.statistics import (
    PerformanceStats,
    compute_stats,
    paper_breakdown,
)

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


# Fuseau d'affichage des trades dans les embeds (récap hebdo, clôtures).
PARIS_TZ = ZoneInfo("Europe/Paris")

# Numéro du jour Python (date.weekday()) : lundi=0 ... dimanche=6.
WEEKDAY_LABELS = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
]


def format_day_time(value: datetime, tz: ZoneInfo = PARIS_TZ) -> str:
    """Date + heure lisibles : "lundi 31/08 20:00" (heure de Paris par défaut)."""
    local = _as_utc(value).astimezone(tz)
    return f"{WEEKDAY_LABELS[local.weekday()]} {local.strftime('%d/%m %H:%M')}"


def scaled_targets(
    action: str,
    entry_price: Decimal | float | str,
    stop_loss: Decimal | float | str,
    multiples: tuple[int, ...] = (1, 2, 3),
) -> tuple[Decimal, ...]:
    """Niveaux de sortie partielle dérivés du bracket : entry ± n × risque.

    Pure présentation (suggestion de gestion humaine) : ni le pipeline, ni la
    validation, ni le paper trading n'utilisent ces niveaux — le TP/SL du
    signal restent la référence de la simulation.
    """
    entry = Decimal(str(entry_price))
    risk = entry - Decimal(str(stop_loss)) if action == "BUY" else Decimal(str(stop_loss)) - entry
    sign = Decimal("1") if action == "BUY" else Decimal("-1")
    return tuple(entry + sign * Decimal(m) * risk for m in multiples)


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
    # Sorties partielles suggérées (scale-out 1/3) : le risque initial
    # (entry - SL) définit TP1/TP2/TP3. Décision de gestion humaine, aucun ordre.
    tp1, tp2, tp3 = scaled_targets(action, entry_price, stop_loss)
    embed.add_field(
        name="Sorties partielles (suggestion)",
        value=(
            f"TP1 : {format_price(tp1)} (+1R)\n"
            f"TP2 : {format_price(tp2)} (+2R)\n"
            f"TP3 : {format_price(tp3)} (+3R)"
        ),
        inline=False,
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


def _paper_line(label: str, stats: PerformanceStats) -> str:
    """Ligne compacte d'une ventilation paper : n · win% · total R."""
    return f"{label} : {stats.total} trades · {stats.win_rate}% · {stats.total_r} R"


def _fmt_r(value: Decimal) -> str:
    """Décimal R lisible sans exposant (``Decimal('100').normalize()`` donnerait ``1E+2``)."""
    return format(value.normalize(), "f")


def _signe_r(value: Decimal) -> str:
    return "+" if value > 0 else ""


def build_stats_embed(
    *,
    total: int,
    par_statut: dict[str, int],
    par_action: dict[str, int],
    par_strategie: dict[str, int],
    paper: "PerformanceStats",
    ouvertes: int = 0,
    filtre: str | None = None,
    par_symbole: dict[str, PerformanceStats] | None = None,
    par_direction: dict[str, PerformanceStats] | None = None,
    sparkline: str = "",
) -> discord.Embed:
    """Statistiques des signaux stockés + paper trading (commande /stats).

    Les performances sont en R-multiples (simulation locale, Phase 21) :
    toujours mentionner le nombre de trades — un échantillon < 30 n'a aucune
    signification statistique. `filtre` (Phases 23-24) affiche les critères
    de filtrage actifs. `par_symbole` / `par_direction` / `sparkline`
    (ventilations et courbe d'équité) ne s'affichent que s'il y a des
    positions clôturées.
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
        pf = "—" if paper.profit_factor is None else str(paper.profit_factor)
        papier = (
            f"{paper.total} clôturées · {ouvertes} ouvertes\n"
            f"Win rate : {paper.win_rate}% ({paper.wins}W / {paper.losses}L)\n"
            f"Total : {paper.total_r} R · Moyenne : {paper.avg_r} R · Médiane : {paper.median_r} R\n"
            f"Profit factor : {pf} · Max drawdown : {paper.max_drawdown_r} R\n"
            f"Meilleur : {paper.best_r} R · Pire : {paper.worst_r} R"
        )
        if sparkline:
            papier += f"\nÉquité : {sparkline}"
    else:
        papier = f"Aucune position clôturée ({ouvertes} ouverte(s))"
    embed.add_field(name="Paper trading (R)", value=papier, inline=False)
    if paper.total > 0 and par_symbole:
        embed.add_field(
            name="Par symbole (R)",
            value="\n".join(_paper_line(k, v) for k, v in par_symbole.items()),
            inline=True,
        )
    if paper.total > 0 and par_direction:
        lignes = [
            _paper_line("🟢 BUY" if k == "BUY" else "🔴 SELL", v)
            for k, v in sorted(par_direction.items())
        ]
        embed.add_field(name="Par direction (R)", value="\n".join(lignes), inline=True)
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


# --- Suivi des trades : clôtures et récap quotidien ---

ORANGE = 0xE67E22  # récap quotidien


def build_health_alert_embed(*, component: str, detail: str, resolved: bool) -> discord.Embed:
    """Embed d'une alerte de santé (transition OK -> KO) ou de sa résolution.

    Publié dans le salon des logs par `HealthAlertService` : purement
    informatif, aucune action automatique n'est effectuée.
    """
    if resolved:
        embed = discord.Embed(
            title=f"✅ Résolu — {component}", description=detail, color=GREEN
        )
    else:
        embed = discord.Embed(
            title=f"🚨 Alerte santé — {component}", description=detail, color=RED
        )
    embed.set_footer(text="Contrôle automatique — aucune action effectuée")
    return embed


def build_closure_embed(outcome) -> discord.Embed:
    """Embed d'une position paper clôturée (TP ou SL détecté à la bougie).

    `outcome` : `app.paper_trading.engine.CloseOutcome`.
    """
    is_tp = outcome.exit_reason == "TP"
    titre = "✅ Take Profit atteint" if is_tp else "❌ Stop Loss atteint"
    is_buy = outcome.action == "BUY"
    embed = discord.Embed(
        title=f"{titre} — {outcome.symbol}",
        color=GREEN if is_tp else RED,
    )
    embed.add_field(name="Position", value="LONG 🟢" if is_buy else "SHORT 🔴", inline=True)
    embed.add_field(name="Strategy", value=strategy_label(outcome.strategy), inline=True)
    embed.add_field(name="Entry", value=format_price(outcome.entry_price), inline=True)
    embed.add_field(name="Sortie", value=format_price(outcome.exit_price), inline=True)
    signe = "+" if outcome.result_r > 0 else ""
    embed.add_field(name="Résultat", value=f"{signe}{outcome.result_r.normalize()} R", inline=True)
    embed.add_field(
        name="Ouvert le", value=format_day_time(outcome.opened_at), inline=True
    )
    embed.add_field(
        name="Clôturé le", value=format_day_time(outcome.closed_at), inline=True
    )
    embed.set_footer(
        text="Paper trading — simulation locale, aucun ordre réel · heure de Paris"
    )
    return embed


def build_weekly_recap_embed(
    *,
    debut: datetime,
    fin: datetime,
    ouvertes_semaine: list,
    cloturees_semaine: list,
    en_cours: list,
    cloturees_semaine_precedente: list | None = None,
) -> discord.Embed:
    """Embed du récap hebdomadaire (vendredi 22h, heure locale configurée).

    - `ouvertes_semaine` : lignes (PaperPosition, Signal, nom de stratégie)
      ouvertes sur les 7 derniers jours ;
    - `cloturees_semaine` : lignes (PaperPosition, PaperTrade, Signal, nom de
      stratégie) clôturées sur les 7 derniers jours ;
    - `cloturees_semaine_precedente` : mêmes lignes pour les 7 jours
      précédents (comparaison du bilan) ;
    - `en_cours` : TOUTES les positions ouvertes — elles restent dans chaque
      récap jusqu'à leur TP/SL.
    """
    embed = discord.Embed(
        title=(
            f"📅 Récap hebdomadaire — semaine du "
            f"{debut.strftime('%d/%m/%Y')} au {fin.strftime('%d/%m/%Y')}"
        ),
        color=ORANGE,
    )

    lignes_ouvertes = [
        f"{'🟢' if signal.action == 'BUY' else '🔴'} **{signal.symbol}** "
        f"{'LONG' if signal.action == 'BUY' else 'SHORT'} · "
        f"{strategy_label(strategy_name)} · entry {format_price(signal.entry_price)} · "
        f"SL {format_price(signal.stop_loss)} · TP {format_price(signal.take_profit)} · "
        f"ouvert le {format_day_time(position.opened_at)}"
        for position, signal, strategy_name in ouvertes_semaine
    ]
    embed.add_field(
        name=f"📈 Nouvelles positions ({len(ouvertes_semaine)})",
        value="\n".join(lignes_ouvertes) or "—",
        inline=False,
    )

    lignes_cloturees = []
    for _position, trade, signal, strategy_name in cloturees_semaine:
        gagnant = trade.exit_reason == "TP"
        signe = "+" if _position.result_r > 0 else ""
        lignes_cloturees.append(
            f"{'✅' if gagnant else '❌'} **{signal.symbol}** "
            f"{'LONG' if signal.action == 'BUY' else 'SHORT'} · "
            f"{'TP' if gagnant else 'SL'} @ {format_price(trade.exit_price)} · "
            f"{signe}{_position.result_r.normalize()} R · "
            f"{format_day_time(_position.opened_at)} → {format_day_time(trade.closed_at)}"
        )
    embed.add_field(
        name=f"🏁 Clôturées cette semaine ({len(cloturees_semaine)})",
        value="\n".join(lignes_cloturees) or "—",
        inline=False,
    )

    lignes_cours = [
        f"{'🟢' if signal.action == 'BUY' else '🔴'} **{signal.symbol}** "
        f"{'LONG' if signal.action == 'BUY' else 'SHORT'} · "
        f"depuis le {format_day_time(position.opened_at)} · "
        f"entry {format_price(signal.entry_price)} · "
        f"SL {format_price(signal.stop_loss)} · TP {format_price(signal.take_profit)}"
        for position, signal, strategy_name in en_cours
    ]
    embed.add_field(
        name=f"⏳ En cours ({len(en_cours)})",
        value="\n".join(lignes_cours) or "—",
        inline=False,
    )

    # --- Bilan enrichi (stats R, comparaison S-1, ventilation direction) ---
    stats = compute_stats([p.result_r for p, _t, _s, _st in cloturees_semaine])
    if stats.total > 0:
        pf = "—" if stats.profit_factor is None else _fmt_r(stats.profit_factor)
        bilan = (
            f"{stats.total} trades · win {stats.win_rate}% · "
            f"{_signe_r(stats.total_r)}{_fmt_r(stats.total_r)} R\n"
            f"Moyenne {_signe_r(stats.avg_r)}{_fmt_r(stats.avg_r)} R · "
            f"Médiane {_signe_r(stats.median_r)}{_fmt_r(stats.median_r)} R · PF {pf}\n"
            f"Meilleur {_signe_r(stats.best_r)}{_fmt_r(stats.best_r)} R · "
            f"Pire {_signe_r(stats.worst_r)}{_fmt_r(stats.worst_r)} R"
        )
        stats_precedentes = compute_stats(
            [p.result_r for p, _t, _s, _st in cloturees_semaine_precedente or []]
        )
        if stats_precedentes.total > 0:
            delta = stats.total_r - stats_precedentes.total_r
            bilan += (
                f"\nSemaine précédente : "
                f"{_signe_r(stats_precedentes.total_r)}{_fmt_r(stats_precedentes.total_r)} R "
                f"({stats_precedentes.total} trades) · "
                f"Δ {_signe_r(delta)}{_fmt_r(delta)} R"
            )
    else:
        bilan = "Aucune clôture cette semaine"
    embed.add_field(name="Résultat de la semaine", value=bilan, inline=False)

    if stats.total > 0:
        _symbole, par_direction = paper_breakdown(
            [(p.result_r, s.symbol, s.action) for p, _t, s, _st in cloturees_semaine]
        )
        lignes_direction = [
            _paper_line("🟢 BUY" if action == "BUY" else "🔴 SELL", dir_stats)
            for action, dir_stats in sorted(par_direction.items())
        ]
        embed.add_field(
            name="Par direction (R)",
            value="\n".join(lignes_direction),
            inline=False,
        )
    embed.set_footer(
        text="Paper trading — simulation locale en R, aucun ordre réel · "
        "heures de Paris · les positions en cours restent affichées "
        "chaque semaine jusqu'à TP/SL"
    )
    return embed
