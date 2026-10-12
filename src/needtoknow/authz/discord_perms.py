"""Discord's permission calculation, as a pure function.

Follows Discord's documented algorithm for a member's permissions in a channel:
https://discord.com/developers/docs/topics/permissions#permission-overwrites

No I/O and no discord.py: the inputs are plain values, so every rule can be
tested directly. This module must not import Discord or database code.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum

# Permission bits (Discord's values).
ADMINISTRATOR = 1 << 3
VIEW_CHANNEL = 1 << 10
READ_MESSAGE_HISTORY = 1 << 16
ALL_PERMISSIONS = (1 << 64) - 1


class ChannelType(IntEnum):
    """Discord's channel type numbers (stored as-is in `channels.kind`)."""

    GUILD_TEXT = 0
    GUILD_VOICE = 2
    GUILD_CATEGORY = 4
    GUILD_ANNOUNCEMENT = 5
    ANNOUNCEMENT_THREAD = 10
    PUBLIC_THREAD = 11
    PRIVATE_THREAD = 12
    GUILD_STAGE_VOICE = 13
    GUILD_FORUM = 15
    GUILD_MEDIA = 16


THREAD_TYPES = frozenset(
    {ChannelType.ANNOUNCEMENT_THREAD, ChannelType.PUBLIC_THREAD, ChannelType.PRIVATE_THREAD}
)


class OverwriteType(IntEnum):
    """Who a channel overwrite targets (Discord's values)."""

    ROLE = 0
    MEMBER = 1


@dataclass(frozen=True, slots=True)
class Overwrite:
    """A per-channel rule that forces permission bits off (deny) or on (allow)."""

    id: int  # role ID or user ID, depending on type
    type: OverwriteType
    allow: int = 0
    deny: int = 0


@dataclass(frozen=True, slots=True)
class Channel:
    id: int
    kind: ChannelType
    parent_id: int | None = None  # category, or the parent channel of a thread
    overwrites: tuple[Overwrite, ...] = ()


@dataclass(frozen=True, slots=True)
class GuildState:
    """What the calculation needs to know about one server."""

    guild_id: int
    owner_id: int
    # role ID -> server-wide permission bits. @everyone's role ID is the guild ID.
    roles: Mapping[int, int] = field(default_factory=dict)
    channels: Mapping[int, Channel] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Member:
    user_id: int
    role_ids: frozenset[int]  # @everyone may or may not be included; it always applies


class UnknownChannelError(LookupError):
    """The channel (or a thread's parent) isn't in the guild state."""


def base_permissions(guild: GuildState, member: Member) -> int:
    """Server-wide permissions: @everyone's plus every role the member has."""
    if member.user_id == guild.owner_id:
        return ALL_PERMISSIONS
    permissions = guild.roles.get(guild.guild_id, 0)
    for role_id in member.role_ids:
        # A role missing from the state grants nothing.
        permissions |= guild.roles.get(role_id, 0)
    if permissions & ADMINISTRATOR:
        return ALL_PERMISSIONS
    return permissions


def channel_permissions(guild: GuildState, member: Member, channel_id: int) -> int:
    """The member's permissions in a channel, after its overwrites.

    Threads have no overwrites of their own: their parent channel's apply.
    A channel's category isn't consulted. Discord copies category overwrites
    onto channels that are synced with it, so the channel's own list is complete.
    """
    base = base_permissions(guild, member)
    if base == ALL_PERMISSIONS:  # owner or administrator: overwrites can't take anything away
        return ALL_PERMISSIONS

    channel = _overwrite_source(guild, channel_id)
    by_target = {(o.type, o.id): o for o in channel.overwrites}
    permissions = base

    # 1. The @everyone overwrite.
    everyone = by_target.get((OverwriteType.ROLE, guild.guild_id))
    if everyone is not None:
        permissions = (permissions & ~everyone.deny) | everyone.allow

    # 2. The member's role overwrites, combined: all denies, then all allows.
    #    So if one of their roles allows a bit and another denies it, allow wins.
    allow = deny = 0
    for role_id in member.role_ids - {guild.guild_id}:
        overwrite = by_target.get((OverwriteType.ROLE, role_id))
        if overwrite is not None:
            allow |= overwrite.allow
            deny |= overwrite.deny
    permissions = (permissions & ~deny) | allow

    # 3. An overwrite for this specific member beats everything above.
    own = by_target.get((OverwriteType.MEMBER, member.user_id))
    if own is not None:
        permissions = (permissions & ~own.deny) | own.allow

    return permissions


def can_read(permissions: int) -> bool:
    """Reading a channel's history needs both View Channel and Read Message History."""
    needed = VIEW_CHANNEL | READ_MESSAGE_HISTORY
    return permissions & needed == needed


def _overwrite_source(guild: GuildState, channel_id: int) -> Channel:
    channel = guild.channels.get(channel_id)
    if channel is None:
        raise UnknownChannelError(channel_id)
    if channel.kind in THREAD_TYPES:
        parent = guild.channels.get(channel.parent_id) if channel.parent_id else None
        if parent is None:
            raise UnknownChannelError(channel.parent_id)
        return parent
    return channel
