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
    bot = NeedToKnowBot()

    mentions = bot.allowed_mentions
    assert mentions is not None
    assert not (mentions.everyone or mentions.users or mentions.roles or mentions.replied_user)


async def test_ping_is_registered_and_server_only() -> None:
    bot = NeedToKnowBot()

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
