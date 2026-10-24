"""Translate discord.py objects into plain records.

This is the only module in discord_state/ that knows discord.py's types. The
records it produces are what the database stores and what the permission engine
in authz/ reads.
"""

from collections.abc import Iterable
from dataclasses import dataclass

import discord

from needtoknow.authz.discord_perms import ChannelType, Overwrite, OverwriteType

type DiscordChannel = discord.abc.GuildChannel | discord.Thread


@dataclass(frozen=True, slots=True)
class ChannelRecord:
    id: int
    kind: ChannelType
    name: str
    parent_id: int | None  # category for channels, parent channel for threads
    overwrites: tuple[Overwrite, ...]


@dataclass(frozen=True, slots=True)
class GuildSnapshot:
    """A server's full permission state, as sent when the bot connects or joins."""

    guild_id: int
    owner_id: int
    roles: dict[int, int]  # role ID -> permission bits, including @everyone
    channels: tuple[ChannelRecord, ...]  # channels and active threads


def channel_record(channel: DiscordChannel) -> ChannelRecord | None:
    """None for channel types this code doesn't know (Discord adds new ones)."""
    try:
        kind = ChannelType(channel.type.value)
    except ValueError:
        return None
    if isinstance(channel, discord.Thread):
        # Threads have no overwrites; their parent's apply.
        return ChannelRecord(channel.id, kind, channel.name, channel.parent_id, ())
    return ChannelRecord(
        channel.id, kind, channel.name, channel.category_id, overwrite_records(channel)
    )


def overwrite_records(channel: discord.abc.GuildChannel) -> tuple[Overwrite, ...]:
    records = []
    for target, overwrite in channel.overwrites.items():
        allow, deny = overwrite.pair()
        records.append(Overwrite(target.id, _target_type(target), allow.value, deny.value))
    return tuple(sorted(records, key=lambda o: (o.type, o.id)))


class IncompleteGuildError(ValueError):
    """The guild data is missing something the permission state needs."""


def guild_snapshot(guild: discord.Guild) -> GuildSnapshot:
    # The owner gets every permission, so an unknown owner can't be guessed at.
    if guild.owner_id is None:
        raise IncompleteGuildError(guild.id)
    channels: Iterable[DiscordChannel] = [*guild.channels, *guild.threads]
    return GuildSnapshot(
        guild_id=guild.id,
        owner_id=guild.owner_id,
        roles={role.id: role.permissions.value for role in guild.roles},
        channels=tuple(r for c in channels if (r := channel_record(c)) is not None),
    )


def _target_type(target: discord.Role | discord.Member | discord.Object) -> OverwriteType:
    # Targets missing from discord.py's cache (members, usually: the bot doesn't
    # request the member list) arrive as Object, with the type it would have had.
    if isinstance(target, discord.Role):
        return OverwriteType.ROLE
    if isinstance(target, discord.Object) and target.type is discord.Role:
        return OverwriteType.ROLE
    return OverwriteType.MEMBER
