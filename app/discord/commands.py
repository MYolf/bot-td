"""Commandes slash du bot (Phase 3 : /status ; Phase 20 : les cinq commandes).

Toutes les réponses sont `ephemeral` (back-office : visible uniquement par
l'utilisateur qui invoque). Toute lecture passe par le repository (jamais de
SQL inline) ; les erreurs remontent au handler global défini dans bot.py.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.config.settings import Settings
from app.database.database import ping_database, session_scope
from app.database.repository import PaperRepository, SignalRepository, StrategyRepository
from app.discord.embeds import (
    build_signal_embed_from_row,
    build_signals_list_embed,
    build_stats_embed,
    build_strategies_embed,
)
from app.paper_trading.statistics import compute_stats, equity_sparkline, paper_breakdown

logger = logging.getLogger(__name__)


def build_status_content(app_env: str, database_online: bool) -> str:
    """Construit la réponse de /status (fonction pure, testable sans Discord).

    Format conforme à Projet.md §30 : Bot / Database / Webhook / Environment.
    """
    database = "ONLINE" if database_online else "OFFLINE"
    return (
        "🟢 Bot: ONLINE\n"
        f"Database: {database}\n"
        "Webhook: ONLINE\n"
        f"Environment: {app_env.upper()}"
    )


@app_commands.command(name="status", description="État du bot (database, webhook, environnement)")
async def status_command(interaction: discord.Interaction) -> None:
    settings: Settings = interaction.client.bot_settings  # type: ignore[attr-defined]
    database_online = await ping_database()
    content = build_status_content(settings.app_env, database_online)
    await interaction.response.send_message(content, ephemeral=True)
    logger.info("/status invoqué par %s", interaction.user)


@app_commands.command(name="lastsignal", description="Afficher le dernier signal reçu")
async def lastsignal_command(interaction: discord.Interaction) -> None:
    async with session_scope() as session:
        rows = await SignalRepository(session).get_latest(limit=1)
    if not rows:
        await interaction.response.send_message(
            "Aucun signal pour le moment.", ephemeral=True
        )
        return
    signal, strategy_name = rows[0]
    embed = build_signal_embed_from_row(signal, strategy_name)
    await interaction.response.send_message(embed=embed, ephemeral=True)
    logger.info("/lastsignal invoqué par %s", interaction.user)


@app_commands.command(name="signals", description="Afficher les derniers signaux")
@app_commands.describe(limite="Nombre de signaux à afficher (1 à 10, 5 par défaut)")
async def signals_command(
    interaction: discord.Interaction,
    limite: app_commands.Range[int, 1, 10] = 5,
) -> None:
    async with session_scope() as session:
        rows = await SignalRepository(session).get_latest(limit=limite)
    if not rows:
        await interaction.response.send_message(
            "Aucun signal pour le moment.", ephemeral=True
        )
        return
    embed = build_signals_list_embed(rows)
    await interaction.response.send_message(embed=embed, ephemeral=True)
    logger.info("/signals invoqué par %s (limite=%d)", interaction.user, limite)


@app_commands.command(name="stats", description="Statistiques des signaux et paper trading")
@app_commands.describe(
    timeframe="Filtrer par timeframe (valeurs autorisées : 5, 15, 30, 60, 240, D)",
    strategie="Filtrer par stratégie (ex : momentum_v1)",
)
async def stats_command(
    interaction: discord.Interaction,
    timeframe: str | None = None,
    strategie: str | None = None,
) -> None:
    """Statistiques filtrables par timeframe et/ou stratégie (Phases 23-24)."""
    settings: Settings = interaction.client.bot_settings  # type: ignore[attr-defined]
    if timeframe is not None and timeframe not in settings.allowed_timeframes:
        await interaction.response.send_message(
            f"Timeframe inconnu : `{timeframe}`. Valeurs autorisées : "
            + ", ".join(f"`{t}`" for t in settings.allowed_timeframes),
            ephemeral=True,
        )
        return
    if strategie is not None and strategie not in settings.allowed_strategies:
        await interaction.response.send_message(
            f"Stratégie inconnue : `{strategie}`. Valeurs autorisées : "
            + ", ".join(f"`{s}`" for s in settings.allowed_strategies),
            ephemeral=True,
        )
        return

    filtres = [f"timeframe={timeframe}", f"stratégie={strategie}"]
    filtre = " · ".join(f for f in filtres if "=None" not in f) or None

    async with session_scope() as session:
        repository = SignalRepository(session)
        total = await repository.count_all(timeframe=timeframe, strategy=strategie)
        par_statut = await repository.count_by_status(timeframe=timeframe, strategy=strategie)
        par_action = await repository.count_by_action(timeframe=timeframe, strategy=strategie)
        par_strategie = await repository.count_by_strategy(timeframe=timeframe)
        paper_repository = PaperRepository(session)
        rows = await paper_repository.closed_rows(
            timeframe=timeframe, strategy=strategie
        )
        paper = compute_stats([result_r for result_r, _, _ in rows])
        par_symbole, par_direction = paper_breakdown(rows)
        ouvertes = await paper_repository.count_open()
    embed = build_stats_embed(
        total=total,
        par_statut=par_statut,
        par_action=par_action,
        par_strategie=par_strategie,
        paper=paper,
        ouvertes=ouvertes,
        filtre=filtre,
        par_symbole=par_symbole,
        par_direction=par_direction,
        sparkline=equity_sparkline([result_r for result_r, _, _ in rows]),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    logger.info(
        "/stats invoqué par %s timeframe=%s strategie=%s",
        interaction.user,
        timeframe,
        strategie,
    )


@app_commands.command(name="strategy", description="Stratégies enregistrées")
async def strategy_command(interaction: discord.Interaction) -> None:
    async with session_scope() as session:
        strategies = await StrategyRepository(session).list_all()
        compte = await SignalRepository(session).count_by_strategy()
    embed = build_strategies_embed(strategies, compte)
    await interaction.response.send_message(embed=embed, ephemeral=True)
    logger.info("/strategy invoqué par %s", interaction.user)


def register(bot: commands.Bot) -> None:
    """Enregistre toutes les commandes slash sur l'arbre du bot."""
    bot.tree.add_command(status_command)
    bot.tree.add_command(lastsignal_command)
    bot.tree.add_command(signals_command)
    bot.tree.add_command(stats_command)
    bot.tree.add_command(strategy_command)
