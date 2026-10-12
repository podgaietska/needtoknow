"""Table tests for Discord's permission calculation, one rule or edge case per row."""

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from needtoknow.authz.discord_perms import (
    ADMINISTRATOR,
    ALL_PERMISSIONS,
    READ_MESSAGE_HISTORY,
    VIEW_CHANNEL,
    Channel,
    ChannelType,
    GuildState,
    Member,
    Overwrite,
    OverwriteType,
    UnknownChannelError,
    can_read,
    channel_permissions,
)

GUILD = 1  # also @everyone's role ID
OWNER, ALICE, BOB = 10, 11, 12
MEMBER_ROLE, MOD_ROLE, UNKNOWN_ROLE = 100, 101, 999
CHANNEL = 200
SEND_MESSAGES = 1 << 11
READ = VIEW_CHANNEL | READ_MESSAGE_HISTORY

ALICE_M = Member(ALICE, frozenset({MEMBER_ROLE}))
BOB_M = Member(BOB, frozenset({MEMBER_ROLE, MOD_ROLE}))


def role(role_id: int, allow: int = 0, deny: int = 0) -> Overwrite:
    return Overwrite(role_id, OverwriteType.ROLE, allow, deny)


def user(user_id: int, allow: int = 0, deny: int = 0) -> Overwrite:
    return Overwrite(user_id, OverwriteType.MEMBER, allow, deny)


def guild(
    *overwrites: Overwrite,
    everyone: int = READ | SEND_MESSAGES,
    member_role: int = 0,
    mod_role: int = 0,
) -> GuildState:
    return GuildState(
        guild_id=GUILD,
        owner_id=OWNER,
        roles={GUILD: everyone, MEMBER_ROLE: member_role, MOD_ROLE: mod_role},
        channels={CHANNEL: Channel(CHANNEL, ChannelType.GUILD_TEXT, overwrites=overwrites)},
    )


@dataclass(frozen=True)
class Case:
    name: str
    state: GuildState
    member: Member
    reads: bool


CASES = [
    Case("no overwrites: @everyone's server permissions apply", guild(), ALICE_M, True),
    Case("@everyone lacks View Channel server-wide", guild(everyone=SEND_MESSAGES), ALICE_M, False),
    Case("@everyone overwrite denies view", guild(role(GUILD, deny=VIEW_CHANNEL)), ALICE_M, False),
    Case(
        "private channel: @everyone denied, Moderator allowed (Alice)",
        guild(role(GUILD, deny=VIEW_CHANNEL), role(MOD_ROLE, allow=VIEW_CHANNEL)),
        ALICE_M,
        False,
    ),
    Case(
        "private channel: @everyone denied, Moderator allowed (Bob)",
        guild(role(GUILD, deny=VIEW_CHANNEL), role(MOD_ROLE, allow=VIEW_CHANNEL)),
        BOB_M,
        True,
    ),
    Case(
        "one role's allow beats another role's deny",
        guild(role(MEMBER_ROLE, deny=VIEW_CHANNEL), role(MOD_ROLE, allow=VIEW_CHANNEL)),
        BOB_M,
        True,
    ),
    Case(
        "a role's deny beats the @everyone overwrite's allow",
        guild(
            role(GUILD, allow=VIEW_CHANNEL),
            role(MEMBER_ROLE, deny=VIEW_CHANNEL),
            everyone=READ_MESSAGE_HISTORY,
        ),
        ALICE_M,
        False,
    ),
    Case(
        "@everyone in the member's role list isn't applied twice",
        guild(
            role(GUILD, allow=VIEW_CHANNEL),
            role(MEMBER_ROLE, deny=VIEW_CHANNEL),
            everyone=READ_MESSAGE_HISTORY,
        ),
        Member(ALICE, frozenset({GUILD, MEMBER_ROLE})),
        False,
    ),
    Case(
        "a member deny beats a role allow",
        guild(role(MOD_ROLE, allow=VIEW_CHANNEL), user(BOB, deny=VIEW_CHANNEL)),
        BOB_M,
        False,
    ),
    Case(
        "a member allow beats @everyone's deny (VIP channel)",
        guild(role(GUILD, deny=VIEW_CHANNEL), user(ALICE, allow=VIEW_CHANNEL)),
        ALICE_M,
        True,
    ),
    Case(
        "a member overwrite for someone else doesn't apply",
        guild(role(GUILD, deny=VIEW_CHANNEL), user(BOB, allow=VIEW_CHANNEL)),
        ALICE_M,
        False,
    ),
    Case(
        "an overwrite for a role the member lacks doesn't apply",
        guild(role(GUILD, deny=VIEW_CHANNEL), role(MOD_ROLE, allow=VIEW_CHANNEL)),
        ALICE_M,
        False,
    ),
    Case(
        "allow and deny of the same bit in one overwrite: allow wins",
        guild(role(MEMBER_ROLE, allow=VIEW_CHANNEL, deny=VIEW_CHANNEL)),
        ALICE_M,
        True,
    ),
    Case(
        "View Channel without Read Message History can't read",
        guild(everyone=VIEW_CHANNEL),
        ALICE_M,
        False,
    ),
    Case(
        "Read Message History denied in the channel",
        guild(role(GUILD, deny=READ_MESSAGE_HISTORY)),
        ALICE_M,
        False,
    ),
    Case(
        "a role missing from the state grants nothing",
        guild(everyone=0),
        Member(ALICE, frozenset({UNKNOWN_ROLE})),
        False,
    ),
    Case(
        "a role's server-wide permissions add to @everyone's",
        guild(everyone=VIEW_CHANNEL, member_role=READ_MESSAGE_HISTORY),
        ALICE_M,
        True,
    ),
    Case(
        "Administrator ignores every overwrite",
        guild(
            role(GUILD, deny=READ),
            role(MEMBER_ROLE, deny=READ),
            user(ALICE, deny=READ),
            member_role=ADMINISTRATOR,
        ),
        ALICE_M,
        True,
    ),
    Case(
        "the owner ignores every overwrite, with no roles at all",
        guild(role(GUILD, deny=READ), user(OWNER, deny=READ), everyone=0),
        Member(OWNER, frozenset()),
        True,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_permission_rules(case: Case) -> None:
    permissions = channel_permissions(case.state, case.member, CHANNEL)

    assert can_read(permissions) is case.reads


def test_owner_and_administrator_get_every_permission() -> None:
    state = guild(role(GUILD, deny=READ), member_role=ADMINISTRATOR)

    assert channel_permissions(state, Member(OWNER, frozenset()), CHANNEL) == ALL_PERMISSIONS
    assert channel_permissions(state, ALICE_M, CHANNEL) == ALL_PERMISSIONS


def test_overwrites_only_change_the_bits_they_name() -> None:
    state = guild(role(GUILD, deny=SEND_MESSAGES))

    permissions = channel_permissions(state, ALICE_M, CHANNEL)

    assert permissions == READ


# Threads and categories.

PARENT, THREAD, PRIVATE_THREAD, CATEGORY = 300, 301, 302, 400


def with_thread(*parent_overwrites: Overwrite) -> GuildState:
    return GuildState(
        guild_id=GUILD,
        owner_id=OWNER,
        roles={GUILD: READ},
        channels={
            PARENT: Channel(PARENT, ChannelType.GUILD_TEXT, overwrites=parent_overwrites),
            # A thread's own overwrites are never used (Discord threads have none).
            THREAD: Channel(THREAD, ChannelType.PUBLIC_THREAD, PARENT, (role(GUILD, allow=READ),)),
            PRIVATE_THREAD: Channel(PRIVATE_THREAD, ChannelType.PRIVATE_THREAD, PARENT),
        },
    )


@pytest.mark.parametrize("thread", [THREAD, PRIVATE_THREAD])
def test_threads_use_their_parents_overwrites(thread: int) -> None:
    open_parent = with_thread()
    closed_parent = with_thread(role(GUILD, deny=VIEW_CHANNEL))

    assert can_read(channel_permissions(open_parent, ALICE_M, thread))
    assert not can_read(channel_permissions(closed_parent, ALICE_M, thread))


def test_category_overwrites_are_not_consulted() -> None:
    state = GuildState(
        guild_id=GUILD,
        owner_id=OWNER,
        roles={GUILD: READ},
        channels={
            CATEGORY: Channel(
                CATEGORY, ChannelType.GUILD_CATEGORY, overwrites=(role(GUILD, deny=READ),)
            ),
            CHANNEL: Channel(CHANNEL, ChannelType.GUILD_TEXT, CATEGORY),
        },
    )

    assert can_read(channel_permissions(state, ALICE_M, CHANNEL))


def test_unknown_channel_raises() -> None:
    with pytest.raises(UnknownChannelError):
        channel_permissions(guild(), ALICE_M, 12345)


def test_thread_with_unknown_parent_raises() -> None:
    state = GuildState(
        guild_id=GUILD,
        owner_id=OWNER,
        roles={GUILD: READ},
        channels={THREAD: Channel(THREAD, ChannelType.PUBLIC_THREAD, PARENT)},
    )

    with pytest.raises(UnknownChannelError):
        channel_permissions(state, ALICE_M, THREAD)


# The engine stays pure: nothing in authz/ may reach Discord or the database.

AUTHZ_DIR = Path(__file__).resolve().parents[2] / "src" / "needtoknow" / "authz"
FORBIDDEN = ("discord", "psycopg", "psycopg_pool", "sqlalchemy", "needtoknow.db", "needtoknow.bot")


@pytest.mark.parametrize("path", sorted(AUTHZ_DIR.glob("*.py")), ids=lambda p: p.name)
def test_authz_does_no_io(path: Path) -> None:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    bad = {m for m in imported for f in FORBIDDEN if m == f or m.startswith(f + ".")}
    assert not bad
