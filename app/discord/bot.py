"""Bot Discord du projet bot-td (Phase 3).

Le bot est un outil d'AFFICHAGE uniquement (statut, signaux, statistiques).
Il ne prend aucune décision de trading et n'exécute jamais d'ordre.

Il tourne dans le même process que FastAPI : démarré depuis le lifespan de
`app.main` via `asyncio.create_task`, arrêté proprement au shutdown.
"""

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.config.settings import Settings

logger = logging.getLogger(__name__)


class SignalBot(commands.Bot):
    """Bot de signaux — intents minimaux, aucune permission privilégiée."""

    def __init__(self, settings: Settings):
        # Intents minimaux : guilds suffit pour envoyer des messages et
        # synchroniser les commandes. Pas de presences, pas de members
        # (privileged).
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)
        self.bot_settings = settings

    async def setup_hook(self) -> None:
        """Initialisation asynchrone : enregistrement et sync des commandes."""
        from app.discord import commands as bot_commands

        bot_commands.register(self)

        # Handler d'erreurs global des commandes slash : log + message sobre,
        # jamais de stacktrace en chat public.
        @self.tree.error
        async def on_app_command_error(
            interaction: discord.Interaction,
            error: app_commands.AppCommandError,
        ) -> None:
            logger.exception(
                "Erreur commande slash %s : %s",
                getattr(interaction.command, "name", "<inconnue>"),
                error,
            )
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Une erreur est survenue. Consultez les logs du serveur.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Une erreur est survenue. Consultez les logs du serveur.",
                    ephemeral=True,
                )

        # Sync limitée à la guild en développement (effet immédiat dans
        # Discord, pas de propagation globale).
        guild_id = self.bot_settings.discord_guild_id
        if guild_id is not None:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Commandes slash synchronisées sur la guild (%d)", len(synced))
        else:
            synced = await self.tree.sync()
            logger.info("Commandes slash synchronisées globalement (%d)", len(synced))

    async def on_ready(self) -> None:
        logger.info("Bot Discord connecté en tant que %s", self.user)


def create_bot(settings: Settings) -> SignalBot:
    """Construit le bot à partir de la configuration."""
    return SignalBot(settings)


async def run_bot(bot: SignalBot, token: str) -> None:
    """Démarre le bot sans jamais laisser une exception remonter au lifespan.

    Le token vient uniquement de `.env` et n'est jamais loggé.
    """
    try:
        await bot.start(token)
    except discord.LoginFailure:
        logger.error(
            "Connexion Discord impossible : token invalide (DISCORD_BOT_TOKEN). "
            "L'API continue de fonctionner sans le bot."
        )
    except asyncio.CancelledError:
        await bot.close()
        raise
    except Exception:
        logger.exception("Erreur inattendue du bot Discord")
        await bot.close()
