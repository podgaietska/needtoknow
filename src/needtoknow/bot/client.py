"""The Discord client: gateway connection, intents, and slash command registration."""

import logging

import discord
from discord import app_commands

from needtoknow.bot.commands import register_commands

log = logging.getLogger(__name__)


def build_intents() -> discord.Intents:
    """The gateway events we subscribe to, and nothing more."""
    intents = discord.Intents.none()
    intents.guilds = True  # servers, channels, roles, threads
    intents.guild_messages = True  # message create / edit / delete in servers
    # Privileged: without it, message events arrive with empty text. Must also be
    # switched on for the application in the Discord Developer Portal.
    intents.message_content = True
    return intents


class NeedToKnowBot(discord.Client):
    def __init__(self, *, dev_guild_id: int | None = None) -> None:
        super().__init__(
            intents=build_intents(),
            # Nothing the bot sends can ping @everyone, roles, or users.
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.tree = app_commands.CommandTree(self)
        self.dev_guild_id = dev_guild_id
        register_commands(self.tree)

    async def setup_hook(self) -> None:
        """Runs once after login, before connecting to the gateway."""
        await sync_commands(self.tree, self.dev_guild_id)

    async def on_ready(self) -> None:
        log.info(
            "ready",
            extra={"bot_user_id": self.user.id if self.user else None, "guilds": len(self.guilds)},
        )


async def sync_commands(tree: app_commands.CommandTree, dev_guild_id: int | None) -> None:
    """Upload the command definitions to Discord.

    Global commands can take a while to appear in every server. In development,
    copying them to one server makes them show up there immediately.
    """
    if dev_guild_id is None:
        synced = await tree.sync()
        log.info("commands synced", extra={"scope": "global", "count": len(synced)})
    else:
        guild = discord.Object(id=dev_guild_id)
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        log.info(
            "commands synced",
            extra={"scope": "guild", "guild_id": dev_guild_id, "count": len(synced)},
        )
