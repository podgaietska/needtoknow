"""Which channels a member can read, and which channel governs a piece of content.

Content is attributed to the channel whose permissions decide who may read it:
a text, announcement, or forum channel governs itself, and a public thread
(including a forum post) is governed by its parent. Everything else (private
threads, voice channels, categories) is never readable through the bot.
"""

from needtoknow.authz.discord_perms import (
    ChannelType,
    GuildState,
    Member,
    can_read,
    channel_permissions,
)

# Channels with a message history the bot reads.
READABLE_TYPES = frozenset(
    {ChannelType.GUILD_TEXT, ChannelType.GUILD_ANNOUNCEMENT, ChannelType.GUILD_FORUM}
)
# Threads that everyone who can read the parent can read. Private threads also
# need membership, which isn't tracked, so they are excluded.
PUBLIC_THREAD_TYPES = frozenset({ChannelType.ANNOUNCEMENT_THREAD, ChannelType.PUBLIC_THREAD})


def permission_channel_id(guild: GuildState, channel_id: int) -> int | None:
    """The channel whose permissions govern content posted in `channel_id`.

    None means the content isn't readable through the bot at all.
    """
    channel = guild.channels.get(channel_id)
    if channel is None:
        return None
    if channel.kind in READABLE_TYPES:
        return channel.id
    if channel.kind in PUBLIC_THREAD_TYPES and channel.parent_id is not None:
        parent = guild.channels.get(channel.parent_id)
        if parent is not None and parent.kind in READABLE_TYPES:
            return parent.id
    return None


def visible_channels(guild: GuildState, member: Member) -> frozenset[int]:
    """The readable channels this member can read right now.

    Computed fresh from the member's roles and the guild state on every call;
    nothing about who can read what is stored. Threads aren't listed: their
    content is attributed to their parent (see `permission_channel_id`).
    """
    return frozenset(
        channel.id
        for channel in guild.channels.values()
        if channel.kind in READABLE_TYPES
        and can_read(channel_permissions(guild, member, channel.id))
    )
