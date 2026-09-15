"""Embeds Discord des signaux (Phase 12).

Fonctions pures : constructibles et testables sans connexion Discord.
Format conforme à Projet.md §22, lisible sur mobile : titre normalisé
(🟢 LONG SIGNAL / 🔴 SHORT SIGNAL), champs nommés, prix lisibles, heure UTC.
"""

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import discord

from app.paper_trading.statistics import (
    PerformanceStats,
    compute_stats,
    paper_breakdown,
)
from app.signals.scoring import SCORE_COMPONENT_MAX

GREEN = 0x2ECC71  # LONG
RED = 0xE74C3C  # SHORT

# Correspondance timeframe interne -> affichage
_TIMEFRAME_LABELS = {"60": "1h", "240": "4h", "D": "1D"}


def timeframe_label(timeframe: str) -> str:
    """Rend le timeframe lisible ("15" -> "15m")."""
    if timeframe in _TIMEFRAME_LABELS:
        return _TIMEFRAME_LABELS[timeframe]
    return f"{timeframe}m"


def timeframe_minutes(timeframe: str) -> int:
    """Durée d'une bougie en minutes ("15" -> 15, "D" -> 1440)."""
    if timeframe.upper() == "D":
        return 1440
    return int(timeframe)


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


# --- Pips : distance de prix lisible (pure présentation) ---

# Taille d'un pip par symbole. Par défaut 1 unité de la paire USDT (1 $) :
# BTC TP1 à +1R ≈ +1 000 pips, ETH ≈ +35 pips. Convention choisie par
# l'utilisateur (2026-09-11).
PIP_SIZES: dict[str, Decimal] = {}


def pip_size(symbol: str) -> Decimal:
    """Taille d'un pip pour le symbole (1 USDT par défaut)."""
    return PIP_SIZES.get(symbol.upper(), Decimal("1"))


def format_pips(
    symbol: str, price_from: Decimal | float | str, price_to: Decimal | float | str
) -> str:
    """Distance en pips entre deux prix : « +1,048 pips » (arrondi au pip)."""
    distance = abs(Decimal(str(price_to)) - Decimal(str(price_from))) / pip_size(symbol)
    pips = int(distance.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return f"+{pips:,} pips"


# --- Nombres à la française (récap hebdo, performance) ---


def _fmt_fr(value: Decimal) -> str:
    """Décimal lisible à la française : virgule décimale, sans exposant."""
    return format(value.normalize(), "f").replace(".", ",")


def format_r_fr(value: Decimal) -> str:
    """R signé à la française : « +1R », « +0,13R », « -1R »."""
    signe = "+" if value > 0 else ""
    return f"{signe}{_fmt_fr(value)}R"


# --- Setup : indicateurs derrière chaque composante du score ---

# Libellés par stratégie (purement informatif, pour l'embed de signal).
# momentum_v1 : EMA 50/200 (tendance), RSI 14 (momentum), MACD 12/26/9.
STRATEGY_SETUPS: dict[str, dict[str, str]] = {
    "momentum_v1": {
        "score_trend": "Tendance — EMA 50/200",
        "score_momentum": "Momentum — RSI 14",
        "score_macd": "MACD 12/26/9",
    },
}

# Libellés génériques (stratégie inconnue de la map).
GENERIC_SETUP: dict[str, str] = {
    "score_trend": "Tendance",
    "score_momentum": "Momentum",
    "score_macd": "MACD",
    "score_volume": "Volume",
    "score_structure": "Structure",
    "score_htf": "Tendance HTF",
}


def setup_lines(
    strategy: str, components: dict[str, int] | None
) -> list[str]:
    """Lignes du champ « Setup » : indicateur et points par composante.

    Sans composantes (anciens signaux), liste les indicateurs de la stratégie
    si elle est connue, sinon rien (pas de champ).
    """
    labels = STRATEGY_SETUPS.get(strategy, GENERIC_SETUP)
    if components:
        return [
            f"{labels.get(field, GENERIC_SETUP[field])} : "
            f"{points}/{SCORE_COMPONENT_MAX[field]}"
            for field, points in components.items()
        ]
    if strategy in STRATEGY_SETUPS:
        return list(STRATEGY_SETUPS[strategy].values())
    return []


def score_sur_100(score: int, components: dict[str, int] | None) -> str:
    """Score affiché sur 100, recalibré sur le maximum des composantes envoyées.

    momentum_v1 n'évalue que 55 points de barème : 45/55 s'affiche « 82/100 ».
    Sans composantes (anciens signaux) : score brut en points, pas d'échelle
    inventée. Le score reste un indicateur interne, pas une probabilité.
    """
    if components:
        maxi = sum(SCORE_COMPONENT_MAX[field] for field in components)
        if maxi > 0:
            return f"{round(score * 100 / maxi)}/100"
    return f"{score} pts"


# --- Symboles : nom court et emoji (récap hebdo) ---

SYMBOL_EMOJIS = {"BTC": "₿", "ETH": "♦️"}


def _symbole_court(symbol: str) -> str:
    """BTCUSDT -> BTC (les paires non USDT restent telles quelles)."""
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def _symbol_emoji(symbol: str) -> str:
    return SYMBOL_EMOJIS.get(_symbole_court(symbol), "🔹")


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
    multiples: tuple[float, ...] = (1, 2, 3),
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
    score_components: dict[str, int] | None = None,
    trade_number: int | None = None,
    macro_level: str | None = None,
    macro_note: str | None = None,
) -> discord.Embed:
    """Construit l'embed d'un signal validé (BUY/SELL).

    `score` (Phase 26) : qualité interne du signal, affichée recalibrée sur
    100 (voir `score_sur_100`) uniquement si la stratégie en envoie les
    composantes. `score_components` : composantes présentes, pour le champ
    « Setup » (indicateurs et points).
    `trade_number` : numéro séquentiel du trade (#16), affiché s'il est connu.
    `macro_level`/`macro_note` (display-only, MACRO.md §10) : contexte macro
    HIGH/EXTREME — une ligne d'avertissement factuelle, jamais un blocage.
    """
    is_buy = action == "BUY"
    embed = discord.Embed(
        title=f"{'🟢 LONG SIGNAL' if is_buy else '🔴 SHORT SIGNAL'} — {symbol}",
        color=GREEN if is_buy else RED,
    )
    # Ligne 1 : identité du trade.
    if trade_number is not None:
        embed.add_field(name="Trade", value=f"#{trade_number}", inline=True)
    embed.add_field(name="Strategy", value=strategy_label(strategy), inline=True)
    embed.add_field(name="Timeframe", value=timeframe_label(timeframe), inline=True)
    # Ligne 2 : le bracket, avec la distance du TP en pips.
    embed.add_field(name="Entry", value=format_price(entry_price), inline=True)
    embed.add_field(name="Stop Loss", value=format_price(stop_loss), inline=True)
    embed.add_field(
        name="Take Profit",
        value=(
            f"{format_price(take_profit)}\n"
            f"{format_pips(symbol, entry_price, take_profit)}"
        ),
        inline=True,
    )
    # Ligne 3 : métriques et horodatage.
    embed.add_field(
        name="Risk/Reward", value=format_risk_reward(risk_reward), inline=True
    )
    score_value = (
        score_sur_100(score, score_components) if score is not None else None
    )
    if score_value is not None:
        embed.add_field(name="Signal Score", value=score_value, inline=True)
    embed.add_field(
        name="Signal Time",
        value=_as_utc(signal_time).strftime("%H:%M:%S UTC"),
        inline=True,
    )
    # Setup : indicateurs utilisés par la stratégie et points par composante.
    lignes_setup = setup_lines(strategy, score_components)
    if lignes_setup:
        embed.add_field(name="Setup", value="\n".join(lignes_setup), inline=False)
    # Sorties partielles suggérées (scale-out 1/3) : le risque initial
    # (entry - SL) définit TP1/TP2. TP3 n'a PAS de take profit : le solde
    # reste ouvert (gestion libre). BE à +1,5R (mi-chemin TP1→TP2) : quand
    # ce niveau est atteint, le solde est protégé au prix d'entrée — avec
    # TP1 pris à +1R, un retour à l'entrée laisse le trade à +1R sans perte.
    # Décision de gestion humaine, aucun ordre.
    tp1, tp2 = scaled_targets(action, entry_price, stop_loss, multiples=(1, 2))
    be_trigger = scaled_targets(action, entry_price, stop_loss, multiples=(1.5,))[0]
    embed.add_field(
        name="Sorties partielles (suggestion)",
        value=(
            f"TP1 : {format_price(tp1)} (+1R · {format_pips(symbol, entry_price, tp1)})\n"
            f"TP2 : {format_price(tp2)} (+2R · {format_pips(symbol, entry_price, tp2)})\n"
            f"TP3 : ouvert\n"
            f"BE : SL → entrée à {format_price(be_trigger)} (+1,5R)"
        ),
        inline=False,
    )
    # Macro (display-only) : une seule ligne factuelle, jamais de jargon de
    # décision (« bloqué », « validé »...) — le signal est toujours émis.
    if macro_note is not None and macro_level in ("HIGH", "EXTREME"):
        emoji = "⚠️" if macro_level == "HIGH" else "🔴"
        embed.add_field(
            name="Macro",
            value=f"{emoji} {macro_note} · risque {macro_level}",
            inline=False,
        )
    if score_value is not None:
        # Projet.md §40 : jamais présenté comme une probabilité de gain.
        embed.set_footer(text="Score = qualité interne du signal (pas une probabilité de gain)")
    return embed


# --- Signaux à l'avance (pré-alertes moteur, engine/advance.py) ---

ORANGE = 0xE67E22  # annulation de pré-alerte


def build_advance_embed(
    *,
    action: str,
    symbol: str,
    strategy: str,
    timeframe: str,
    entry_price: Decimal | float | str,
    stop_loss: Decimal | float | str,
    take_profit: Decimal | float | str,
    risk_reward: Decimal | float | str,
    score: int | None = None,
    score_components: dict[str, int] | None = None,
    expires_in: int | None = None,
    touch_rate: float | None = None,
) -> discord.Embed:
    """Embed d'une PRÉ-ALERTE : niveau limite annoncé avant la clôture.

    Même présentation qu'un signal (l'utilisateur se positionne comme pour un
    signal), avec les différences essentielles : pas d'heure (la bougie est
    encore en formation), pas de numéro de trade (rien n'est enregistré en
    base), et un avertissement explicite — touché ne signifie pas confirmé
    (étude 4 ans : ~1 touche filtrée sur 2 se confirme).

    Mode anticipatif (ANTICIPATION.md v1.1) : ``expires_in`` (validité en
    bougies) et ``touch_rate`` (taux de toucher MESURÉ sur 4 ans) sont
    fournis — le niveau est annoncé dès l'ouverture de la bougie, avec sa
    durée de validité. Le taux affiché est une fréquence observée, NI une
    probabilité de gain NI un avantage de prix (fill ≈ neutre).
    """
    is_buy = action == "BUY"
    if expires_in is not None:
        description = (
            "⏳ **Signal à l'avance** — annoncé dès l'ouverture de la bougie.\n"
            "Placez un ordre **limite** à l'entrée. Le trade ne démarre que si "
            "le niveau est touché **puis confirmé** à la clôture : un message "
            "« ✅ Signal validé » suivra, sinon une annulation — ne pas garder "
            "la position. Sans touche à l'expiration, retirez l'ordre."
        )
    else:
        description = (
            "⏳ **Signal à l'avance** — la bougie est encore en formation.\n"
            "Placez un ordre **limite** à l'entrée : le trade ne démarre que si "
            "le niveau est touché **puis confirmé** à la clôture. Sinon, une "
            "annulation suivra — ne pas garder la position."
        )
    embed = discord.Embed(
        title=f"{'🟢 LONG SIGNAL' if is_buy else '🔴 SHORT SIGNAL'} — {symbol}",
        color=GREEN if is_buy else RED,
        description=description,
    )
    embed.add_field(name="Strategy", value=strategy_label(strategy), inline=True)
    embed.add_field(name="Timeframe", value=timeframe_label(timeframe), inline=True)
    embed.add_field(
        name="Entry", value=f"{format_price(entry_price)} (limite)", inline=True
    )
    embed.add_field(name="Stop Loss", value=format_price(stop_loss), inline=True)
    embed.add_field(
        name="Take Profit",
        value=(
            f"{format_price(take_profit)}\n"
            f"{format_pips(symbol, entry_price, take_profit)}"
        ),
        inline=True,
    )
    embed.add_field(
        name="Risk/Reward", value=format_risk_reward(risk_reward), inline=True
    )
    score_value = (
        score_sur_100(score, score_components) if score is not None else None
    )
    if score_value is not None:
        embed.add_field(name="Signal Score", value=score_value, inline=True)
    lignes_setup = setup_lines(strategy, score_components)
    if lignes_setup:
        embed.add_field(name="Setup", value="\n".join(lignes_setup), inline=False)
    tp1, tp2 = scaled_targets(action, entry_price, stop_loss, multiples=(1, 2))
    be_trigger = scaled_targets(action, entry_price, stop_loss, multiples=(1.5,))[0]
    embed.add_field(
        name="Sorties partielles (suggestion)",
        value=(
            f"TP1 : {format_price(tp1)} (+1R · {format_pips(symbol, entry_price, tp1)})\n"
            f"TP2 : {format_price(tp2)} (+2R · {format_pips(symbol, entry_price, tp2)})\n"
            f"TP3 : ouvert\n"
            f"BE : SL → entrée à {format_price(be_trigger)} (+1,5R)"
        ),
        inline=False,
    )
    if expires_in is not None:
        minutes = timeframe_minutes(timeframe) * expires_in
        embed.add_field(
            name="Validité",
            value=f"Expire dans {expires_in} bougie(s) (≈ {minutes} min) si le "
            "niveau n'est pas touché",
            inline=False,
        )
    footer = (
        "Pré-alerte émise avant la clôture — non comptabilisée dans les statistiques"
    )
    if touch_rate is not None:
        footer = (
            f"Niveau atteint dans ~{round(touch_rate * 100)} % des cas (4 ans de "
            "données) — fréquence observée, ni probabilité de gain ni avantage "
            "de prix · " + footer
        )
    embed.set_footer(text=footer)
    return embed


def build_advance_invalidated_embed(
    *, symbol: str, timeframe: str, action: str, level: Decimal | float | str
) -> discord.Embed:
    """Embed d'annulation : niveau touché mais bougie non confirmée à la clôture.

    C'est le message le plus important du cycle : si l'ordre limite de
    l'utilisateur a été rempli, il doit décharger la position (étude 4 ans :
    décharge médiane ≈ −0,1 %, 70-84 % des cas < 0,2 %).
    """
    is_buy = action == "BUY"
    embed = discord.Embed(
        title=f"❌ Pré-alerte annulée — {symbol}",
        color=ORANGE,
        description=(
            "Le niveau a été **touché** mais la bougie n'a **pas confirmé** le "
            "signal à la clôture.\n"
            "Si votre ordre limite a été rempli : **déchargez la position**."
        ),
    )
    embed.add_field(
        name="Direction", value="LONG" if is_buy else "SHORT", inline=True
    )
    embed.add_field(name="Niveau touché", value=format_price(level), inline=True)
    embed.add_field(name="Timeframe", value=timeframe_label(timeframe), inline=True)
    return embed


def build_advance_confirmed_embed(
    *, symbol: str, timeframe: str, action: str, level: Decimal | float | str
) -> discord.Embed:
    """Embed « Signal validé » : niveau touché PUIS confirmé à la clôture.

    Amendement v1.1 (ANTICIPATION.md) : envoyé en PLUS du signal officiel
    (qui suit le pipeline normal et part dans le salon des signaux). Si
    l'ordre limite de l'utilisateur a été rempli au niveau, il est en
    position — l'avantage de fill mesuré est ≈ neutre (aucune promesse).
    """
    is_buy = action == "BUY"
    embed = discord.Embed(
        title=f"✅ Signal validé — {symbol}",
        color=GREEN,
        description=(
            "Le niveau annoncé a été **touché** puis **confirmé** à la clôture : "
            "le signal officiel vient d'être envoyé (salon des signaux).\n"
            "Si votre ordre limite a été rempli, vous êtes en position — tenez "
            "le trade comme un signal normal."
        ),
    )
    embed.add_field(
        name="Direction", value="LONG" if is_buy else "SHORT", inline=True
    )
    embed.add_field(name="Niveau d'entrée", value=format_price(level), inline=True)
    embed.add_field(name="Timeframe", value=timeframe_label(timeframe), inline=True)
    embed.set_footer(
        text="Avantage d'entrée mesuré ≈ neutre (4 ans de données) — "
        "l'intérêt est l'anticipation, pas un meilleur prix"
    )
    return embed


def build_advance_expired_embed(
    *,
    symbol: str,
    timeframe: str,
    action: str,
    level: Decimal | float | str,
    expires_in: int | None = None,
) -> discord.Embed:
    """Embed d'expiration : le niveau n'a pas été touché à l'horizon.

    L'ordre limite de l'utilisateur n'a pas été exécuté : il faut le
    retirer pour ne pas laisser d'ordre en attente sur un niveau périmé.
    """
    is_buy = action == "BUY"
    embed = discord.Embed(
        title=f"⌛ Pré-alerte expirée — {symbol}",
        color=GREY,
        description=(
            "Le niveau annoncé n'a **pas été atteint** dans le délai de "
            "validité.\nSi un ordre limite était placé : **retirez-le** "
            "(il n'a pas été exécuté)."
        ),
    )
    embed.add_field(
        name="Direction", value="LONG" if is_buy else "SHORT", inline=True
    )
    embed.add_field(name="Niveau non atteint", value=format_price(level), inline=True)
    if expires_in is not None:
        embed.add_field(
            name="Validité écoulée",
            value=f"{expires_in} bougie(s)",
            inline=True,
        )
    embed.add_field(name="Timeframe", value=timeframe_label(timeframe), inline=True)
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
    components = {
        field: getattr(signal, field)
        for field in SCORE_COMPONENT_MAX
        if getattr(signal, field) is not None
    }
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
        score_components=components or None,
        trade_number=signal.sequence_number,
        macro_level=signal.macro_level,
        macro_note=signal.macro_note,
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
            f"{emoji} #{signal.sequence_number} **{signal.symbol}** · "
            f"{strategy_label(strategy_name)} "
            f"· {timeframe_label(signal.timeframe)} · {moment} · {signal.status}"
        )
    embed.description = "\n".join(lines)
    return embed


def _paper_line(label: str, stats: PerformanceStats) -> str:
    """Ligne compacte d'une ventilation paper : n · win% · total R."""
    return f"{label} : {stats.total} trades · {stats.win_rate}% · {stats.total_r} R"


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


def build_be_alert_embed(alert) -> discord.Embed:
    """Embed d'un déclencheur break-even atteint (salon dédié signal-be).

    `alert` : `app.paper_trading.engine.BeAlert`. Rappel de gestion humaine :
    au niveau +1,5R, le solde de la position peut être protégé au prix
    d'entrée. Pure information, aucun ordre (règle absolue du projet).
    """
    numero = f"Trade #{alert.sequence_number}" if alert.sequence_number is not None else alert.symbol
    embed = discord.Embed(
        title=f"🛡️ Break-even atteint — {numero}",
        color=0xF1C40F,  # jaune : action de gestion, ni gain ni perte
    )
    embed.add_field(name="Symbole", value=alert.symbol, inline=True)
    embed.add_field(
        name="Position", value="LONG 🟢" if alert.action == "BUY" else "SHORT 🔴", inline=True
    )
    embed.add_field(
        name="Déclencheur (+1,5R)", value=format_price(alert.be_trigger), inline=True
    )
    embed.add_field(
        name="Action suggérée",
        value=f"SL → entrée ({format_price(alert.entry_price)})",
        inline=True,
    )
    embed.set_footer(
        text="Rappel de gestion — décision humaine, aucun ordre automatique"
    )
    return embed


def build_tp_progress_embed(alert) -> discord.Embed:
    """Embed d'une sortie partielle TP1/TP2 validée (salon dédié TP).

    `alert` : `app.paper_trading.engine.TpAlert`. Affiche l'état des TP
    connus : validés ✅ ou en cours ⏳, avec la distance en pips depuis
    l'entrée. Rappel de gestion humaine (scale-out), aucun ordre.
    """
    numero = f"Trade #{alert.sequence_number}" if alert.sequence_number is not None else alert.symbol
    embed = discord.Embed(
        title=f"✅ TP{alert.level} validé — {numero}",
        color=GREEN,
    )
    embed.add_field(name="Symbole", value=alert.symbol, inline=True)
    embed.add_field(
        name="Position", value="LONG 🟢" if alert.action == "BUY" else "SHORT 🔴", inline=True
    )
    lignes = []
    for niveau, prix, valide in alert.niveaux:
        etat = "✅ validé" if valide else "⏳ en cours"
        pips = format_pips(alert.symbol, alert.entry_price, prix)
        lignes.append(f"TP{niveau} : {etat} ({format_price(prix)} · {pips})")
    embed.add_field(name="Take Profits", value="\n".join(lignes), inline=False)
    embed.set_footer(
        text="Sorties partielles — gestion manuelle, aucun ordre automatique"
    )
    return embed


def build_closure_embed(outcome) -> discord.Embed:
    """Embed d'une position paper clôturée (TP ou SL détecté à la bougie).

    `outcome` : `app.paper_trading.engine.CloseOutcome`.
    """
    is_be = outcome.exit_reason == "BE"
    is_tp = outcome.exit_reason == "TP"
    if is_be:
        titre, couleur = "🛡️ Break-even touché", 0xF1C40F  # jaune : ni gain ni perte
    elif is_tp:
        titre, couleur = "✅ Take Profit atteint", GREEN
    else:
        titre, couleur = "❌ Stop Loss atteint", RED
    is_buy = outcome.action == "BUY"
    embed = discord.Embed(
        title=f"{titre} — {outcome.symbol}",
        color=couleur,
    )
    if outcome.sequence_number is not None:
        embed.add_field(name="Trade", value=f"#{outcome.sequence_number}", inline=True)
    embed.add_field(name="Position", value="LONG 🟢" if is_buy else "SHORT 🔴", inline=True)
    embed.add_field(name="Strategy", value=strategy_label(outcome.strategy), inline=True)
    embed.add_field(name="Entry", value=format_price(outcome.entry_price), inline=True)
    embed.add_field(name="Sortie", value=format_price(outcome.exit_price), inline=True)
    if is_be:
        resultat = "+0R"  # sortie à l'entrée : solde préservé, ni gain ni perte
    else:
        signe = "+" if outcome.result_r > 0 else ""
        resultat = f"{signe}{outcome.result_r.normalize()} R"
    embed.add_field(name="Résultat", value=resultat, inline=True)
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
    cloturees_semaine: list,
    en_cours: list,
) -> discord.Embed:
    """Embed du récap hebdomadaire (vendredi 22h, heure locale configurée).

    Format simplifié validé par l'utilisateur (2026-09-11) : performance de la
    semaine, ventilations symbole/direction, meilleur/pire trade, positions
    encore en cours — sans détail trade par trade.

    - `cloturees_semaine` : lignes (PaperPosition, PaperTrade, Signal, nom de
      stratégie) clôturées sur les 7 derniers jours ;
    - `en_cours` : TOUTES les positions ouvertes — elles restent dans chaque
      récap jusqu'à leur TP/SL.
    """
    embed = discord.Embed(
        title="📊 Récap hebdomadaire",
        description=f"{debut.strftime('%d/%m')} → {fin.strftime('%d/%m')}",
        color=ORANGE,
    )

    stats = compute_stats([p.result_r for p, _t, _s, _st in cloturees_semaine])
    if stats.total > 0:
        pf = "—" if stats.profit_factor is None else _fmt_fr(stats.profit_factor)
        perf = (
            f"{stats.total} trade{'s' if stats.total != 1 else ''}\n"
            f"🟢 {stats.wins} gagnant{'s' if stats.wins != 1 else ''} · "
            f"🔴 {stats.losses} perdant{'s' if stats.losses != 1 else ''}\n"
            f"Winrate : {_fmt_fr(stats.win_rate)} %\n"
            f"Résultat : {format_r_fr(stats.total_r)}\n"
            f"PF : {pf}\n"
            f"Moyenne : {format_r_fr(stats.avg_r)}"
        )
    else:
        perf = "0 trade\nAucune clôture cette semaine"
    embed.add_field(name="📈 Performance", value=perf, inline=False)

    if stats.total > 0:
        par_symbole, par_direction = paper_breakdown(
            [(p.result_r, s.symbol, s.action) for p, _t, s, _st in cloturees_semaine]
        )
        embed.add_field(
            name="Symboles",
            value="\n".join(
                f"{_symbol_emoji(symbole)} {_symbole_court(symbole)} : "
                f"{format_r_fr(symbole_stats.total_r)}"
                for symbole, symbole_stats in par_symbole.items()
            ),
            inline=True,
        )
        embed.add_field(
            name="Directions",
            value="\n".join(
                f"{'🟢 BUY' if a == 'BUY' else '🔴 SELL'} : {format_r_fr(dir_stats.total_r)}"
                for a, dir_stats in sorted(par_direction.items())
            ),
            inline=True,
        )
        embed.add_field(
            name="Extrêmes",
            value=(
                f"🏆 Meilleur : {format_r_fr(stats.best_r)}\n"
                f"📉 Pire : {format_r_fr(stats.worst_r)}"
            ),
            inline=False,
        )

    lignes_cours = [
        f"#{signal.sequence_number} {signal.symbol} "
        f"{'LONG' if signal.action == 'BUY' else 'SHORT'} · "
        f"depuis le {format_day_time(position.opened_at)}"
        for position, signal, _strategy_name in en_cours
    ]
    embed.add_field(
        name=f"⏳ En cours ({len(en_cours)})",
        value="\n".join(lignes_cours) or "—",
        inline=False,
    )

    strategies = {strategy_name for *_reste, strategy_name in en_cours}
    strategies.update(strategy_name for *_a, _b, _c, strategy_name in cloturees_semaine)
    noms = " · ".join(sorted(strategy_label(s) for s in strategies))
    prefixe = f"🤖 {noms} · " if noms else ""
    embed.set_footer(text=f"{prefixe}Paper trading · Aucun ordre réel")
    return embed


def build_performance_embed(
    *,
    periodes: list[tuple[str, Decimal]],
    stats: PerformanceStats,
) -> discord.Embed:
    """Performance paper trading par période (commande /performance).

    `periodes` : couples (libellé, total R) — ex. (« 7 jours », +1R). Les
    métriques de détail portent sur l'historique complet des clôtures.
    """
    embed = discord.Embed(title="📈 Performance", color=PURPLE)
    embed.add_field(
        name="Résultat (R)",
        value="\n".join(f"{libelle} : {format_r_fr(total)}" for libelle, total in periodes),
        inline=False,
    )
    pf = "—" if stats.profit_factor is None else _fmt_fr(stats.profit_factor)
    embed.add_field(
        name="Détail (historique complet)",
        value=(
            f"Winrate : {_fmt_fr(stats.win_rate)} %\n"
            f"Profit Factor : {pf}\n"
            f"Expectancy : {format_r_fr(stats.avg_r)}\n"
            f"Max Drawdown : -{_fmt_fr(stats.max_drawdown_r)}R\n"
            f"Trades : {stats.total}"
        ),
        inline=False,
    )
    embed.set_footer(text="Paper trading — simulation locale en R, aucun ordre réel")
    return embed
