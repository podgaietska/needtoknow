"""Apply gateway events to the stored permission state.

Each event becomes one small write inside `guild_scope()`. Writes for the same
guild run one at a time, in the order the events arrived. If a write fails, the
guild is marked stale and rebuilt from discord.py's in-memory copy of it, which
already reflects the event.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

import discord

from needtoknow.db import Pool, guild_scope
from needtoknow.discord_state import store
from needtoknow.discord_state.convert import (
    DiscordChannel,
    IncompleteGuildError,
    channel_record,
    guild_snapshot,
)
from needtoknow.discord_state.health import GatewayHealth

log = logging.getLogger(__name__)

type Write = Callable[[store.Connection], Awaitable[None]]


class StateSync:
    def __init__(
        self,
        pool: Pool,
        health: GatewayHealth,
        get_guild: Callable[[int], discord.Guild | None],
    ) -> None:
        self._pool = pool
        self._health = health
        self._get_guild = get_guild
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # Whole guilds.

    async def guild_available(self, guild: discord.Guild) -> None:
        """A full snapshot: on connect, on joining a server, or after an outage."""
        await self._save_snapshot(guild)

    async def guild_updated(self, guild: discord.Guild) -> None:
        owner_id = guild.owner_id
        if owner_id is None:
            self._health.guild_stale(guild.id)
            return
        await self._write(
            guild.id, "guild_update", lambda c: store.upsert_guild(c, guild.id, owner_id)
        )

    def guild_unavailable(self, guild_id: int) -> None:
        """A Discord outage: no events for this guild until it's available again."""
        self._health.guild_stale(guild_id)

    async def guild_removed(self, guild_id: int) -> None:
        self._health.guild_stale(guild_id)
        await self._write(guild_id, "guild_remove", lambda c: store.mark_guild_removed(c, guild_id))

    async def resync(self, guild_id: int) -> bool:
        """Rebuild the stored state from discord.py's in-memory copy of the guild."""
        guild = self._get_guild(guild_id)
        if guild is None:
            return False
        return await self._save_snapshot(guild)

    # Roles.

    async def role_saved(self, role: discord.Role) -> None:
        guild_id, bits = role.guild.id, role.permissions.value
        await self._write(
            guild_id, "role_save", lambda c: store.upsert_role(c, guild_id, role.id, bits)
        )

    async def role_deleted(self, role: discord.Role) -> None:
        await self._write(role.guild.id, "role_delete", lambda c: store.delete_role(c, role.id))

    # Channels and threads.

    async def channel_saved(self, channel: DiscordChannel) -> None:
        record = channel_record(channel)
        if record is None:  # a channel type we don't know: nothing to store
            return
        guild_id = channel.guild.id
        await self._write(
            guild_id, "channel_save", lambda c: store.upsert_channel(c, guild_id, record)
        )

    async def channel_deleted(self, guild_id: int, channel_id: int) -> None:
        await self._write(guild_id, "channel_delete", lambda c: store.delete_channel(c, channel_id))

    # Internals.

    async def _save_snapshot(self, guild: discord.Guild) -> bool:
        session = self._health.session  # before the write: see GatewayHealth.guild_synced
        try:
            snapshot = guild_snapshot(guild)
        except IncompleteGuildError:
            log.warning("incomplete guild snapshot", extra={"guild_id": guild.id})
            self._health.guild_stale(guild.id)
            return False
        async with self._locks[guild.id]:
            saved = await self._run(
                guild.id, "snapshot", lambda c: store.save_snapshot(c, snapshot)
            )
        if saved:
            self._health.guild_synced(guild.id, session)
        else:
            self._health.guild_stale(guild.id)
        return saved

    async def _write(self, guild_id: int, event: str, write: Write) -> None:
        async with self._locks[guild_id]:
            saved = await self._run(guild_id, event, write)
        if not saved:
            self._health.guild_stale(guild_id)
            await self.resync(guild_id)

    async def _run(self, guild_id: int, event: str, write: Write) -> bool:
        try:
            async with guild_scope(self._pool, guild_id) as conn:
                await write(conn)
        except Exception:
            log.exception("state write failed", extra={"guild_id": guild_id, "event": event})
            return False
        return True
