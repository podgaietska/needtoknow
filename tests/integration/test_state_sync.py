"""Gateway events, applied through the bot's handlers, produce the right stored state."""

from itertools import count

import discord
import pytest

from needtoknow.authz.discord_perms import (
    READ_MESSAGE_HISTORY,
    VIEW_CHANNEL,
    GuildState,
    Member,
)
from needtoknow.authz.visibility import visible_channels
from needtoknow.bot import NeedToKnowBot
from needtoknow.db import Pool, guild_scope
from needtoknow.discord_state.health import GatewayHealth
from needtoknow.discord_state.store import load_guild_state
from needtoknow.discord_state.sync import StateSync
from tests import fakes

READ = VIEW_CHANNEL | READ_MESSAGE_HISTORY
OWNER, ALICE, BOB = 10, 11, 12

_guild_ids = count(50_000, step=1000)


@pytest.fixture
def guild_id() -> int:
    return next(_guild_ids)


class Riverside:
    """A server with #general, a moderators-only #mod-chat, and a Moderator role."""

    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.mod_role = guild_id + 1
        self.general = guild_id + 10
        self.mod_chat = guild_id + 11
        self.thread = guild_id + 20
        self.alice = Member(ALICE, frozenset())
        self.bob = Member(BOB, frozenset({self.mod_role}))

    def everyone(self, permissions: int = READ) -> discord.Role:
        return fakes.role(self.id, permissions, guild_id=self.id)

    def general_channel(self) -> discord.abc.GuildChannel:
        return fakes.channel(self.general, guild_id=self.id, name="general")

    def mod_chat_channel(self, *, private: bool = True) -> discord.abc.GuildChannel:
        overwrites: dict[fakes.Target, discord.PermissionOverwrite] = {
            discord.Object(self.id, type=discord.Role): fakes.overwrite(deny=VIEW_CHANNEL),
            discord.Object(self.mod_role, type=discord.Role): fakes.overwrite(allow=VIEW_CHANNEL),
        }
        return fakes.channel(
            self.mod_chat,
            guild_id=self.id,
            name="mod-chat",
            overwrites=overwrites if private else {},
        )

    def guild(self) -> discord.Guild:
        return fakes.guild(
            self.id,
            OWNER,
            roles=[self.everyone(), fakes.role(self.mod_role, guild_id=self.id)],
            channels=[self.general_channel(), self.mod_chat_channel()],
        )


async def load(pool: Pool, guild_id: int) -> GuildState | None:
    async with guild_scope(pool, guild_id) as conn:
        return await load_guild_state(conn, guild_id)


async def visible(pool: Pool, guild_id: int, member: Member) -> frozenset[int]:
    state = await load(pool, guild_id)
    assert state is not None
    return visible_channels(state, member)


async def test_gateway_events_keep_the_state_current(pool: Pool, guild_id: int) -> None:
    server = Riverside(guild_id)
    bot = NeedToKnowBot(pool=pool)

    # Connect: Discord sends the full snapshot.
    await bot.on_connect()
    await bot.on_guild_available(server.guild())
    assert bot.health.is_current(guild_id)
    assert await visible(pool, guild_id, server.alice) == {server.general}
    assert await visible(pool, guild_id, server.bob) == {server.general, server.mod_chat}

    # #mod-chat is made public.
    await bot.on_guild_channel_update(
        server.mod_chat_channel(), server.mod_chat_channel(private=False)
    )
    assert await visible(pool, guild_id, server.alice) == {server.general, server.mod_chat}

    # A thread is started in #general, then deleted.
    await bot.on_thread_create(fakes.thread(server.thread, server.general, guild_id=guild_id))
    state = await load(pool, guild_id)
    assert state is not None
    assert state.channels[server.thread].parent_id == server.general
    await bot.on_raw_thread_delete(
        discord.RawThreadDeleteEvent(
            {
                "id": str(server.thread),
                "type": 11,
                "guild_id": str(guild_id),
                "parent_id": str(server.general),
            }
        )
    )
    state = await load(pool, guild_id)
    assert state is not None
    assert server.thread not in state.channels

    # @everyone loses Read Message History server-wide.
    await bot.on_guild_role_update(server.everyone(), server.everyone(VIEW_CHANNEL))
    assert await visible(pool, guild_id, server.alice) == set()

    # #general is deleted.
    await bot.on_guild_channel_delete(server.general_channel())
    state = await load(pool, guild_id)
    assert state is not None
    assert server.general not in state.channels

    # The bot is removed from the server.
    await bot.on_guild_remove(server.guild())
    assert await load(pool, guild_id) is None
    assert not bot.health.is_current(guild_id)


async def test_failed_write_is_repaired_from_discords_copy(pool: Pool, guild_id: int) -> None:
    server = Riverside(guild_id)
    health = GatewayHealth()
    health.session_started()
    sync = StateSync(pool, health, get_guild=lambda _: server.guild())

    # No snapshot was ever saved, so the guild row is missing and this write fails.
    await sync.channel_saved(server.general_channel())

    # The failure triggered a rebuild from discord.py's in-memory guild.
    assert health.is_current(guild_id)
    assert await visible(pool, guild_id, server.bob) == {server.general, server.mod_chat}


async def test_failed_write_without_a_copy_stays_stale(pool: Pool, guild_id: int) -> None:
    server = Riverside(guild_id)
    health = GatewayHealth()
    health.session_started()
    sync = StateSync(pool, health, get_guild=lambda _: None)

    # Fails (no guild row), and discord.py has no copy of the guild to rebuild from.
    await sync.channel_saved(server.general_channel())

    assert not health.is_current(guild_id)
    assert await load(pool, guild_id) is None


async def test_guild_without_owner_is_never_trusted(pool: Pool, guild_id: int) -> None:
    server = Riverside(guild_id)
    guild = server.guild()
    guild.owner_id = None
    health = GatewayHealth()
    health.session_started()
    sync = StateSync(pool, health, get_guild=lambda _: None)

    await sync.guild_available(guild)

    assert not health.is_current(guild_id)
    assert await load(pool, guild_id) is None
