import discord
import pytest

from needtoknow.authz.discord_perms import (
    READ_MESSAGE_HISTORY,
    VIEW_CHANNEL,
    ChannelType,
    Overwrite,
    OverwriteType,
)
from needtoknow.discord_state.convert import (
    ChannelRecord,
    GuildSnapshot,
    IncompleteGuildError,
    channel_record,
    guild_snapshot,
    overwrite_records,
)
from tests import fakes

GUILD, OWNER, ALICE = 1, 10, 11
MOD_ROLE = 101
HIGH_BIT = 1 << 40  # permission bits go far past 32


def test_overwrite_targets_and_bits() -> None:
    channel = fakes.channel(
        200,
        overwrites={
            fakes.role(GUILD): fakes.overwrite(deny=VIEW_CHANNEL),  # cached role
            discord.Object(MOD_ROLE, type=discord.Role): fakes.overwrite(allow=VIEW_CHANNEL),
            discord.Object(ALICE, type=discord.User): fakes.overwrite(allow=HIGH_BIT),
            fakes.member(OWNER): fakes.overwrite(deny=READ_MESSAGE_HISTORY),  # cached member
        },
    )

    assert overwrite_records(channel) == (
        Overwrite(GUILD, OverwriteType.ROLE, deny=VIEW_CHANNEL),
        Overwrite(MOD_ROLE, OverwriteType.ROLE, allow=VIEW_CHANNEL),
        Overwrite(OWNER, OverwriteType.MEMBER, deny=READ_MESSAGE_HISTORY),
        Overwrite(ALICE, OverwriteType.MEMBER, allow=HIGH_BIT),
    )


def test_text_channel_record() -> None:
    channel = fakes.channel(
        200,
        name="mod-chat",
        category_id=199,
        overwrites={fakes.role(GUILD): fakes.overwrite(deny=VIEW_CHANNEL)},
    )

    assert channel_record(channel) == ChannelRecord(
        200,
        ChannelType.GUILD_TEXT,
        "mod-chat",
        199,
        (Overwrite(GUILD, OverwriteType.ROLE, deny=VIEW_CHANNEL),),
    )


def test_forum_record() -> None:
    record = channel_record(fakes.channel(204, type=discord.ChannelType.forum))

    assert record is not None
    assert record.kind is ChannelType.GUILD_FORUM


def test_thread_record_points_at_its_parent() -> None:
    thread = fakes.thread(300, parent_id=204, name="how do I install?")

    assert channel_record(thread) == ChannelRecord(
        300, ChannelType.PUBLIC_THREAD, "how do I install?", 204, ()
    )


def test_unknown_channel_type_is_skipped() -> None:
    assert channel_record(fakes.channel(250, type=99)) is None


def test_guild_snapshot() -> None:
    guild = fakes.guild(
        GUILD,
        OWNER,
        roles=[fakes.role(GUILD, VIEW_CHANNEL), fakes.role(MOD_ROLE, READ_MESSAGE_HISTORY)],
        channels=[fakes.channel(200), fakes.channel(250, type=99)],
        threads=[fakes.thread(300, parent_id=200)],
    )

    assert guild_snapshot(guild) == GuildSnapshot(
        guild_id=GUILD,
        owner_id=OWNER,
        roles={GUILD: VIEW_CHANNEL, MOD_ROLE: READ_MESSAGE_HISTORY},
        channels=(
            ChannelRecord(200, ChannelType.GUILD_TEXT, "channel", None, ()),
            ChannelRecord(300, ChannelType.PUBLIC_THREAD, "thread", 200, ()),
        ),
    )


def test_guild_without_owner_is_refused() -> None:
    guild = fakes.guild(GUILD, OWNER, roles=[], channels=[])
    guild.owner_id = None

    with pytest.raises(IncompleteGuildError):
        guild_snapshot(guild)
