"""Stand-ins for discord.py objects, for tests that never connect to Discord.

They are mocks with a `spec`, so they pass the same isinstance checks as the
real classes and reject attributes the real classes don't have.
"""

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import discord

type Target = discord.Role | discord.Member | discord.Object


def role(role_id: int, permissions: int = 0, *, guild_id: int = 0) -> discord.Role:
    fake = MagicMock(spec=discord.Role)
    fake.id = role_id
    fake.guild = SimpleNamespace(id=guild_id)
    fake.permissions = discord.Permissions(permissions)
    return cast(discord.Role, fake)


def member(user_id: int) -> discord.Member:
    fake = MagicMock(spec=discord.Member)
    fake.id = user_id
    return cast(discord.Member, fake)


def overwrite(allow: int = 0, deny: int = 0) -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite.from_pair(
        discord.Permissions(allow), discord.Permissions(deny)
    )


def channel(
    channel_id: int,
    *,
    guild_id: int = 0,
    type: discord.ChannelType | int = discord.ChannelType.text,
    name: str = "channel",
    category_id: int | None = None,
    overwrites: dict[Target, discord.PermissionOverwrite] | None = None,
) -> discord.abc.GuildChannel:
    fake = MagicMock(spec=discord.TextChannel)
    fake.id = channel_id
    fake.guild = SimpleNamespace(id=guild_id)
    fake.type = SimpleNamespace(value=type) if isinstance(type, int) else type
    fake.name = name
    fake.category_id = category_id
    fake.overwrites = overwrites or {}
    return cast(discord.abc.GuildChannel, fake)


def thread(
    thread_id: int,
    parent_id: int,
    *,
    guild_id: int = 0,
    type: discord.ChannelType = discord.ChannelType.public_thread,
    name: str = "thread",
) -> discord.Thread:
    fake = MagicMock(spec=discord.Thread)
    fake.id = thread_id
    fake.guild = SimpleNamespace(id=guild_id)
    fake.type = type
    fake.name = name
    fake.parent_id = parent_id
    return cast(discord.Thread, fake)


def guild(
    guild_id: int,
    owner_id: int,
    *,
    roles: list[discord.Role],
    channels: list[discord.abc.GuildChannel],
    threads: list[discord.Thread] | None = None,
) -> discord.Guild:
    fake = MagicMock(spec=discord.Guild)
    fake.id = guild_id
    fake.owner_id = owner_id
    fake.roles = roles
    fake.channels = channels
    fake.threads = threads or []
    return cast(discord.Guild, fake)
