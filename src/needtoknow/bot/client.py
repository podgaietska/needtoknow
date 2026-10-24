"""The Discord client: gateway connection, intents, slash commands, and the
event handlers that keep each server's stored permission state current."""

import logging

import discord
from discord import app_commands

from needtoknow.bot.commands import register_commands
from needtoknow.db import Pool
from needtoknow.discord_state.health import GatewayHealth
from needtoknow.discord_state.sync import StateSync

log = logging.getLogger(__name__)

# How long to wait for the database at startup before giving up.
POOL_OPEN_TIMEOUT_SECONDS = 10.0


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
    def __init__(self, *, pool: Pool, dev_guild_id: int | None = None) -> None:
        super().__init__(
            intents=build_intents(),
            # Nothing the bot sends can ping @everyone, roles, or users.
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.tree = app_commands.CommandTree(self)
        self.dev_guild_id = dev_guild_id
        self.pool = pool
        self.health = GatewayHealth()
        self.state = StateSync(pool, self.health, self.get_guild)
        register_commands(self.tree)

    async def setup_hook(self) -> None:
        """Runs once after login, before connecting to the gateway."""
        await self.pool.open(wait=True, timeout=POOL_OPEN_TIMEOUT_SECONDS)
        await sync_commands(self.tree, self.dev_guild_id)

    async def close(self) -> None:
        await super().close()
        await self.pool.close()

    async def on_ready(self) -> None:
        log.info(
            "ready",
            extra={"bot_user_id": self.user.id if self.user else None, "guilds": len(self.guilds)},
        )

    # Gateway connection. on_connect fires only for a new session; a resumed
    # session fires on_resumed instead.

    async def on_connect(self) -> None:
        self.health.session_started()

    async def on_resumed(self) -> None:
        self.health.resumed()

    async def on_disconnect(self) -> None:
        self.health.disconnected()

    # Servers. A full snapshot arrives on connect, on joining, and after an outage.

    async def on_guild_available(self, guild: discord.Guild) -> None:
        await self.state.guild_available(guild)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.state.guild_available(guild)

    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        await self.state.guild_updated(after)

    async def on_guild_unavailable(self, guild: discord.Guild) -> None:
        self.state.guild_unavailable(guild.id)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.state.guild_removed(guild.id)

    # Roles.

    async def on_guild_role_create(self, role: discord.Role) -> None:
        await self.state.role_saved(role)

    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        await self.state.role_saved(after)

    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self.state.role_deleted(role)

    # Channels.

    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await self.state.channel_saved(channel)

    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        await self.state.channel_saved(after)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self.state.channel_deleted(channel.guild.id, channel.id)

    # Threads. The raw delete event also covers threads discord.py hadn't cached.

    async def on_thread_create(self, thread: discord.Thread) -> None:
        await self.state.channel_saved(thread)

    async def on_thread_join(self, thread: discord.Thread) -> None:
        await self.state.channel_saved(thread)

    async def on_thread_update(self, before: discord.Thread, after: discord.Thread) -> None:
        await self.state.channel_saved(after)

    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        await self.state.channel_deleted(payload.guild_id, payload.thread_id)


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
