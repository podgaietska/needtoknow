"""The bot's wiring, without connecting to Discord."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from needtoknow.bot import NeedToKnowBot
from needtoknow.bot.client import build_intents, sync_commands
from needtoknow.bot.commands import ping
from needtoknow.db import create_pool
from tests import fakes


def make_bot() -> NeedToKnowBot:
    # The pool is never opened: these tests don't touch the database.
    return NeedToKnowBot(pool=create_pool("postgresql://unused"))


def fake_interaction(latency: float) -> tuple[discord.Interaction, AsyncMock]:
    send_message = AsyncMock()
    interaction = SimpleNamespace(
        client=SimpleNamespace(latency=latency),
        response=SimpleNamespace(send_message=send_message),
    )
    return cast(discord.Interaction, interaction), send_message


def test_intents_are_only_what_we_use() -> None:
    intents = build_intents()

    enabled = {name for name, value in intents if value}
    assert enabled == {"guilds", "guild_messages", "message_content"}


async def test_bot_never_pings_anyone() -> None:
    bot = make_bot()

    mentions = bot.allowed_mentions
    assert mentions is not None
    assert not (mentions.everyone or mentions.users or mentions.roles or mentions.replied_user)


async def test_ping_is_registered_and_server_only() -> None:
    bot = make_bot()

    command = bot.tree.get_command("ping")
    assert isinstance(command, app_commands.Command)
    assert command.callback is ping
    assert command.guild_only


async def test_ping_replies_privately_with_latency() -> None:
    interaction, send_message = fake_interaction(latency=0.0423)

    await ping(interaction)

    send_message.assert_awaited_once_with("pong (42 ms)", ephemeral=True)


@pytest.mark.parametrize("latency", [float("inf"), float("nan")])
async def test_ping_before_first_heartbeat(latency: float) -> None:
    interaction, send_message = fake_interaction(latency)

    await ping(interaction)

    send_message.assert_awaited_once_with("pong", ephemeral=True)


async def test_sync_globally_without_dev_guild() -> None:
    tree = MagicMock(sync=AsyncMock(return_value=[]))

    await sync_commands(tree, dev_guild_id=None)

    tree.sync.assert_awaited_once_with()
    tree.copy_global_to.assert_not_called()


async def test_sync_to_dev_guild() -> None:
    tree = MagicMock(sync=AsyncMock(return_value=[]))

    await sync_commands(tree, dev_guild_id=1001)

    (guild,) = (call.kwargs["guild"] for call in tree.copy_global_to.call_args_list)
    assert guild.id == 1001
    tree.sync.assert_awaited_once_with(guild=guild)


async def test_gateway_events_update_health() -> None:
    bot = make_bot()

    await bot.on_connect()
    assert bot.health.session == 1

    await bot.on_disconnect()
    await bot.on_resumed()
    assert bot.health.session == 1  # a resume isn't a new session


async def test_events_are_forwarded_to_state_sync() -> None:
    bot = make_bot()
    state = AsyncMock()
    bot.state = state
    guild = fakes.guild(1, 10, roles=[], channels=[])
    role = fakes.role(100, guild_id=1)
    channel = fakes.channel(200, guild_id=1)
    thread = fakes.thread(300, parent_id=200, guild_id=1)

    await bot.on_guild_available(guild)
    await bot.on_guild_join(guild)
    await bot.on_guild_update(guild, guild)
    await bot.on_guild_remove(guild)
    await bot.on_guild_role_create(role)
    await bot.on_guild_role_update(role, role)
    await bot.on_guild_role_delete(role)
    await bot.on_guild_channel_create(channel)
    await bot.on_guild_channel_update(channel, channel)
    await bot.on_guild_channel_delete(channel)
    await bot.on_thread_create(thread)
    await bot.on_thread_update(thread, thread)
    await bot.on_raw_thread_delete(
        discord.RawThreadDeleteEvent({"id": "300", "type": 11, "guild_id": "1", "parent_id": "200"})
    )

    assert state.guild_available.await_count == 2
    state.guild_updated.assert_awaited_once_with(guild)
    state.guild_removed.assert_awaited_once_with(1)
    assert state.role_saved.await_count == 2
    state.role_deleted.assert_awaited_once_with(role)
    assert state.channel_saved.await_count == 4
    assert [c.args for c in state.channel_deleted.await_args_list] == [(1, 200), (1, 300)]
