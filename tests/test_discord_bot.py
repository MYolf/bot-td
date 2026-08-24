"""Tests du bot Discord (Phase 3).

Aucune connexion réseau : la logique est testée avec des fakes, la gestion
d'erreur de connexion est testée en simulant l'échec de bot.start().
"""

import asyncio

import discord
import pytest

from app.config.settings import Settings
from app.discord import commands as bot_commands
from app.discord.bot import SignalBot, create_bot, run_bot


@pytest.fixture
def bot(test_settings: Settings) -> SignalBot:
    return create_bot(test_settings)


class TestBuildStatus:
    """La réponse de /status doit être exactement au format de Projet.md §30."""

    def test_format_exact_db_en_ligne(self):
        content = bot_commands.build_status_content("development", True)
        assert content == (
            "🟢 Bot: ONLINE\n"
            "Database: ONLINE\n"
            "Webhook: ONLINE\n"
            "Environment: DEVELOPMENT"
        )

    def test_format_db_hors_ligne(self):
        content = bot_commands.build_status_content("production", False)
        assert "Database: OFFLINE" in content
        assert "Environment: PRODUCTION" in content


class TestCreationBot:
    def test_intents_minimaux(self, bot: SignalBot):
        # guilds nécessaire ; aucun intent privilégié.
        assert bot.intents.guilds is True
        assert bot.intents.members is False
        assert bot.intents.presences is False

    def test_register_ajoute_status(self, bot: SignalBot):
        bot_commands.register(bot)
        command = bot.tree.get_command("status")
        assert command is not None
        assert command.description  # description non vide pour Discord


class FakeResponse:
    """Enregistre l'appel à send_message sans toucher au réseau."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, content: str | None = None, *, embed=None, ephemeral: bool = False):
        self.sent.append({"content": content, "embed": embed, "ephemeral": ephemeral})


class FakeInteraction:
    """Interaction factice : seul ce dont les commandes ont besoin."""

    def __init__(self, settings: Settings):
        self.client = type("FakeClient", (), {"bot_settings": settings})()
        self.user = "testeur"
        self.response = FakeResponse()


class TestStatusCommand:
    async def test_reponse_ephemeral_au_format_attendu(self, test_settings: Settings, monkeypatch):
        async def ping_ok() -> bool:
            return True

        monkeypatch.setattr(bot_commands, "ping_database", ping_ok)
        interaction = FakeInteraction(test_settings)
        await bot_commands.status_command.callback(interaction)  # type: ignore[attr-defined]
        (appel,) = interaction.response.sent
        assert appel["ephemeral"] is True
        assert appel["content"] == (
            "🟢 Bot: ONLINE\n"
            "Database: ONLINE\n"
            "Webhook: ONLINE\n"
            "Environment: DEVELOPMENT"
        )

    async def test_db_indisponible_affichee_honnetement(self, test_settings: Settings, monkeypatch):
        async def ping_ko() -> bool:
            return False

        monkeypatch.setattr(bot_commands, "ping_database", ping_ko)
        interaction = FakeInteraction(test_settings)
        await bot_commands.status_command.callback(interaction)  # type: ignore[attr-defined]
        (appel,) = interaction.response.sent
        assert "Database: OFFLINE" in appel["content"]


class TestRunBotErreurs:
    async def test_token_invalide_ne_leve_pas(self, bot: SignalBot, caplog):
        """Un token invalide est loggé (erreur) sans faire planter l'API."""

        async def start_echoue(token: str):
            raise discord.LoginFailure("token invalide")

        bot.start = start_echoue  # type: ignore[method-assign]
        await run_bot(bot, "mauvais-token")  # ne doit pas lever
        assert any("token invalide" in r.message.lower() for r in caplog.records)

    async def test_annulation_ferme_le_bot(self, bot: SignalBot):
        ferme = False

        async def start_annule(token: str):
            raise asyncio.CancelledError()

        async def close():
            nonlocal ferme
            ferme = True

        bot.start = start_annule  # type: ignore[method-assign]
        bot.close = close  # type: ignore[method-assign]
        with pytest.raises(asyncio.CancelledError):
            await run_bot(bot, "token")
        assert ferme is True
