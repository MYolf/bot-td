"""Commandes slash du bot (Phase 3 : /status).

Les commandes "back-office" répondent en ephemeral (visible uniquement par
l'utilisateur qui les invoque). Les commandes qui interrogeront la base de
données (Phase 20+) passeront par le repository, jamais de SQL inline.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.config.settings import Settings

logger = logging.getLogger(__name__)

# État affiché pour la base de données : la connexion PostgreSQL arrive en
# Phase 11. On l'affiche honnêtement plutôt que de simuler "connected".
DATABASE_STATUS = "non configurée (Phase 11)"


def build_status_content(app_env: str, database_status: str) -> str:
    """Construit la réponse de /status (fonction pure, testable sans Discord)."""
    return (
        "🟢 Bot opérationnel\n"
        f"Environment: {app_env}\n"
        f"Database: {database_status}\n"
        "Discord: connecté"
    )


@app_commands.command(name="status", description="État du bot (version, environnement)")
async def status_command(interaction: discord.Interaction) -> None:
    """Répond avec l'état du bot (ephemeral : information back-office)."""
    settings: Settings = interaction.client.bot_settings  # type: ignore[attr-defined]
    content = build_status_content(settings.app_env, DATABASE_STATUS)
    await interaction.response.send_message(content, ephemeral=True)
    logger.info("/status invoqué par %s", interaction.user)


def register(bot: commands.Bot) -> None:
    """Enregistre toutes les commandes slash sur l'arbre du bot."""
    bot.tree.add_command(status_command)
