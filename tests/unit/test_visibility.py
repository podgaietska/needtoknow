"""visible_channels() and permission_channel_id() on a small, realistic server."""

import pytest

from needtoknow.authz.discord_perms import (
    READ_MESSAGE_HISTORY,
    VIEW_CHANNEL,
    Channel,
    ChannelType,
    GuildState,
    Member,
    Overwrite,
    OverwriteType,
)
from needtoknow.authz.visibility import permission_channel_id, visible_channels

GUILD = 1
OWNER, ALICE, BOB, CAROL = 10, 11, 12, 13
MEMBER_ROLE, MOD_ROLE = 100, 101
READ = VIEW_CHANNEL | READ_MESSAGE_HISTORY

CATEGORY = 199
GENERAL, EVENTS, MOD_CHAT, VIP_LOUNGE, HELP_FORUM, VOICE = 200, 201, 202, 203, 204, 205
FORUM_POST, MOD_THREAD, PRIVATE_THREAD, ORPHAN_THREAD = 300, 301, 302, 303

hidden_from_everyone = Overwrite(GUILD, OverwriteType.ROLE, deny=VIEW_CHANNEL)

RIVERSIDE = GuildState(
    guild_id=GUILD,
    owner_id=OWNER,
    roles={GUILD: READ, MEMBER_ROLE: 0, MOD_ROLE: 0},
    channels={
        c.id: c
        for c in [
            Channel(CATEGORY, ChannelType.GUILD_CATEGORY),
            Channel(GENERAL, ChannelType.GUILD_TEXT, CATEGORY),
            Channel(EVENTS, ChannelType.GUILD_ANNOUNCEMENT, CATEGORY),
            Channel(
                MOD_CHAT,
                ChannelType.GUILD_TEXT,
                overwrites=(
                    hidden_from_everyone,
                    Overwrite(MOD_ROLE, OverwriteType.ROLE, allow=VIEW_CHANNEL),
                ),
            ),
            Channel(
                VIP_LOUNGE,
                ChannelType.GUILD_TEXT,
                overwrites=(
                    hidden_from_everyone,
                    Overwrite(ALICE, OverwriteType.MEMBER, allow=VIEW_CHANNEL),
                ),
            ),
            Channel(HELP_FORUM, ChannelType.GUILD_FORUM),
            Channel(VOICE, ChannelType.GUILD_VOICE),
            Channel(FORUM_POST, ChannelType.PUBLIC_THREAD, HELP_FORUM),
            Channel(MOD_THREAD, ChannelType.PUBLIC_THREAD, MOD_CHAT),
            Channel(PRIVATE_THREAD, ChannelType.PRIVATE_THREAD, GENERAL),
            Channel(ORPHAN_THREAD, ChannelType.PUBLIC_THREAD, 999),
        ]
    },
)

PUBLIC = {GENERAL, EVENTS, HELP_FORUM}


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (Member(ALICE, frozenset({MEMBER_ROLE})), PUBLIC | {VIP_LOUNGE}),
        (Member(BOB, frozenset({MEMBER_ROLE, MOD_ROLE})), PUBLIC | {MOD_CHAT}),
        (Member(CAROL, frozenset()), PUBLIC),
        (Member(OWNER, frozenset()), PUBLIC | {MOD_CHAT, VIP_LOUNGE}),
    ],
    ids=["alice", "bob", "carol (no roles)", "owner"],
)
def test_visible_channels(member: Member, expected: set[int]) -> None:
    assert visible_channels(RIVERSIDE, member) == expected


def test_losing_a_role_takes_effect_on_the_next_call() -> None:
    bob = Member(BOB, frozenset({MEMBER_ROLE, MOD_ROLE}))
    demoted = Member(BOB, frozenset({MEMBER_ROLE}))

    assert MOD_CHAT in visible_channels(RIVERSIDE, bob)
    assert MOD_CHAT not in visible_channels(RIVERSIDE, demoted)


def test_never_lists_threads_categories_or_voice() -> None:
    visible = visible_channels(RIVERSIDE, Member(OWNER, frozenset()))

    assert not visible & {CATEGORY, VOICE, FORUM_POST, MOD_THREAD, PRIVATE_THREAD}


@pytest.mark.parametrize(
    ("channel_id", "expected"),
    [
        (GENERAL, GENERAL),
        (EVENTS, EVENTS),
        (HELP_FORUM, HELP_FORUM),
        (FORUM_POST, HELP_FORUM),
        (MOD_THREAD, MOD_CHAT),
        (PRIVATE_THREAD, None),
        (ORPHAN_THREAD, None),
        (VOICE, None),
        (CATEGORY, None),
        (12345, None),
    ],
    ids=[
        "text channel",
        "announcement channel",
        "forum",
        "forum post -> forum",
        "public thread -> parent",
        "private thread",
        "thread with unknown parent",
        "voice",
        "category",
        "unknown",
    ],
)
def test_permission_channel(channel_id: int, expected: int | None) -> None:
    assert permission_channel_id(RIVERSIDE, channel_id) == expected
