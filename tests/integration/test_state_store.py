"""Saving and loading permission state, against real Postgres as the app role."""

from itertools import count

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
from needtoknow.authz.visibility import visible_channels
from needtoknow.db import Pool, guild_scope
from needtoknow.discord_state.convert import ChannelRecord, GuildSnapshot
from needtoknow.discord_state.store import (
    delete_channel,
    delete_role,
    load_guild_state,
    mark_guild_removed,
    save_snapshot,
    upsert_channel,
    upsert_guild,
    upsert_role,
)

READ = VIEW_CHANNEL | READ_MESSAGE_HISTORY
HIGH_BIT = 1 << 40
OWNER, ALICE, BOB = 10, 11, 12

_guild_ids = count(5000, step=1000)  # room for each test's role and channel IDs


@pytest.fixture
def guild_id() -> int:
    """A fresh guild per test, so tests don't see each other's rows."""
    return next(_guild_ids)


def riverside(guild_id: int) -> GuildSnapshot:
    mod_role, category = guild_id + 1, guild_id + 2
    general, mod_chat, thread = guild_id + 10, guild_id + 11, guild_id + 20
    return GuildSnapshot(
        guild_id=guild_id,
        owner_id=OWNER,
        roles={guild_id: READ, mod_role: HIGH_BIT},
        channels=(
            ChannelRecord(category, ChannelType.GUILD_CATEGORY, "info", None, ()),
            ChannelRecord(general, ChannelType.GUILD_TEXT, "general", category, ()),
            ChannelRecord(
                mod_chat,
                ChannelType.GUILD_TEXT,
                "mod-chat",
                None,
                (
                    Overwrite(guild_id, OverwriteType.ROLE, deny=VIEW_CHANNEL),
                    Overwrite(mod_role, OverwriteType.ROLE, allow=VIEW_CHANNEL),
                ),
            ),
            ChannelRecord(thread, ChannelType.PUBLIC_THREAD, "meetup", general, ()),
        ),
    )


async def save(pool: Pool, snapshot: GuildSnapshot) -> None:
    async with guild_scope(pool, snapshot.guild_id) as conn:
        await save_snapshot(conn, snapshot)


async def load(pool: Pool, guild_id: int) -> GuildState | None:
    async with guild_scope(pool, guild_id) as conn:
        return await load_guild_state(conn, guild_id)


async def test_snapshot_round_trip(pool: Pool, guild_id: int) -> None:
    snapshot = riverside(guild_id)

    await save(pool, snapshot)
    state = await load(pool, guild_id)

    assert state == GuildState(
        guild_id=guild_id,
        owner_id=OWNER,
        roles=snapshot.roles,
        channels={
            c.id: Channel(c.id, c.kind, c.parent_id, c.overwrites) for c in snapshot.channels
        },
    )


async def test_loaded_state_answers_who_can_read_what(pool: Pool, guild_id: int) -> None:
    await save(pool, riverside(guild_id))
    state = await load(pool, guild_id)
    assert state is not None
    general, mod_chat = guild_id + 10, guild_id + 11

    alice = Member(ALICE, frozenset())
    bob = Member(BOB, frozenset({guild_id + 1}))
    assert visible_channels(state, alice) == {general}
    assert visible_channels(state, bob) == {general, mod_chat}


async def test_new_snapshot_replaces_what_changed_offline(pool: Pool, guild_id: int) -> None:
    first = riverside(guild_id)
    general, mod_chat, thread = guild_id + 10, guild_id + 11, guild_id + 20
    await save(pool, first)
    async with guild_scope(pool, guild_id) as conn:
        await conn.execute("UPDATE channels SET indexing_enabled = true WHERE id = %s", (general,))

    # While offline: the mod role and #mod-chat were deleted, #general was renamed,
    # and the thread was archived (so it's missing from the snapshot).
    second = GuildSnapshot(
        guild_id=guild_id,
        owner_id=OWNER,
        roles={guild_id: READ},
        channels=(ChannelRecord(general, ChannelType.GUILD_TEXT, "lobby", None, ()),),
    )
    await save(pool, second)

    state = await load(pool, guild_id)
    assert state is not None
    assert state.roles == {guild_id: READ}
    assert set(state.channels) == {general, thread}
    async with guild_scope(pool, guild_id) as conn:
        row = await (
            await conn.execute(
                "SELECT name, indexing_enabled FROM channels WHERE id = %s", (general,)
            )
        ).fetchone()
    assert row == ("lobby", True)
    assert mod_chat not in state.channels


async def test_deleting_a_channel_deletes_its_messages(pool: Pool, guild_id: int) -> None:
    await save(pool, riverside(guild_id))
    general = guild_id + 10
    async with guild_scope(pool, guild_id) as conn:
        await conn.execute(
            "INSERT INTO messages (id, guild_id, channel_id, author_id, content, created_at) "
            "VALUES (%s, %s, %s, %s, 'see you saturday', now())",
            (guild_id + 100, guild_id, general, ALICE),
        )

    async with guild_scope(pool, guild_id) as conn:
        await delete_channel(conn, general)
        remaining = await (await conn.execute("SELECT count(*) FROM messages")).fetchone()

    assert remaining == (0,)


async def test_single_role_and_channel_changes(pool: Pool, guild_id: int) -> None:
    await save(pool, riverside(guild_id))
    mod_role, general, mod_chat = guild_id + 1, guild_id + 10, guild_id + 11
    opened = ChannelRecord(mod_chat, ChannelType.GUILD_TEXT, "mod-chat", None, ())
    new_channel = ChannelRecord(guild_id + 12, ChannelType.GUILD_ANNOUNCEMENT, "news", None, ())

    async with guild_scope(pool, guild_id) as conn:
        await upsert_role(conn, guild_id, mod_role, READ)
        await upsert_role(conn, guild_id, guild_id + 3, VIEW_CHANNEL)
        await delete_role(conn, guild_id + 3)
        await upsert_channel(conn, guild_id, opened)
        await upsert_channel(conn, guild_id, new_channel)
        await delete_channel(conn, general)

    state = await load(pool, guild_id)
    assert state is not None
    assert state.roles == {guild_id: READ, mod_role: READ}
    assert state.channels[mod_chat].overwrites == ()
    assert state.channels[new_channel.id].kind is ChannelType.GUILD_ANNOUNCEMENT
    assert general not in state.channels


async def test_removed_guild_loads_nothing_until_it_returns(pool: Pool, guild_id: int) -> None:
    await save(pool, riverside(guild_id))

    async with guild_scope(pool, guild_id) as conn:
        await mark_guild_removed(conn, guild_id)
    assert await load(pool, guild_id) is None

    async with guild_scope(pool, guild_id) as conn:
        await upsert_guild(conn, guild_id, OWNER)
    assert await load(pool, guild_id) is not None


async def test_unknown_channel_kinds_are_skipped(pool: Pool, guild_id: int) -> None:
    await save(pool, riverside(guild_id))
    async with guild_scope(pool, guild_id) as conn:
        await conn.execute(
            "INSERT INTO channels (id, guild_id, kind, name) VALUES (%s, %s, 99, 'future')",
            (guild_id + 50, guild_id),
        )

    state = await load(pool, guild_id)

    assert state is not None
    assert guild_id + 50 not in state.channels


async def test_another_guilds_state_cant_be_loaded(pool: Pool, guild_id: int) -> None:
    await save(pool, riverside(guild_id))
    other = next(_guild_ids)

    async with guild_scope(pool, other) as conn:
        assert await load_guild_state(conn, guild_id) is None
