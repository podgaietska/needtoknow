"""Write a server's permission state to Postgres, and load it back for authz.

Every function takes a connection from `guild_scope()`, so row-level security
limits it to that one guild.
"""

from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb

from needtoknow.authz.discord_perms import (
    THREAD_TYPES,
    Channel,
    ChannelType,
    GuildState,
    Overwrite,
    OverwriteType,
)
from needtoknow.discord_state.convert import ChannelRecord, GuildSnapshot

type Connection = AsyncConnection[TupleRow]

_UPSERT_GUILD = """
    INSERT INTO guilds (id, owner_id, joined_at) VALUES (%s, %s, now())
    ON CONFLICT (id) DO UPDATE SET owner_id = EXCLUDED.owner_id, removed_at = NULL
"""
_UPSERT_ROLE = """
    INSERT INTO roles (id, guild_id, permissions) VALUES (%s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET permissions = EXCLUDED.permissions
"""
# Leaves indexing_enabled and last_message_id alone: those are ours, not Discord's.
_UPSERT_CHANNEL = """
    INSERT INTO channels (id, guild_id, kind, name, parent_id, overwrites)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        kind = EXCLUDED.kind,
        name = EXCLUDED.name,
        parent_id = EXCLUDED.parent_id,
        overwrites = EXCLUDED.overwrites
"""


async def save_snapshot(conn: Connection, snapshot: GuildSnapshot) -> None:
    """Make the stored state match a full snapshot of the server.

    Roles and channels missing from the snapshot were deleted while the bot wasn't
    listening, so they're deleted here too (a channel's messages and chunks go with
    it). Threads are the exception: a snapshot lists only active threads, so a
    missing thread may just be archived, and is kept.
    """
    await upsert_guild(conn, snapshot.guild_id, snapshot.owner_id)
    async with conn.cursor() as cur:
        await cur.executemany(
            _UPSERT_ROLE,
            [(role_id, snapshot.guild_id, bits) for role_id, bits in snapshot.roles.items()],
        )
        await cur.executemany(
            _UPSERT_CHANNEL, [_channel_params(snapshot.guild_id, c) for c in snapshot.channels]
        )
    await conn.execute("DELETE FROM roles WHERE id <> ALL(%s)", (list(snapshot.roles),))
    await conn.execute(
        "DELETE FROM channels WHERE kind <> ALL(%s) AND id <> ALL(%s)",
        ([int(k) for k in THREAD_TYPES], [c.id for c in snapshot.channels]),
    )


async def upsert_guild(conn: Connection, guild_id: int, owner_id: int) -> None:
    """Create or update the guild row. Also clears a previous removal."""
    await conn.execute(_UPSERT_GUILD, (guild_id, owner_id))


async def mark_guild_removed(conn: Connection, guild_id: int) -> None:
    await conn.execute("UPDATE guilds SET removed_at = now() WHERE id = %s", (guild_id,))


async def upsert_role(conn: Connection, guild_id: int, role_id: int, permissions: int) -> None:
    await conn.execute(_UPSERT_ROLE, (role_id, guild_id, permissions))


async def delete_role(conn: Connection, role_id: int) -> None:
    await conn.execute("DELETE FROM roles WHERE id = %s", (role_id,))


async def upsert_channel(conn: Connection, guild_id: int, record: ChannelRecord) -> None:
    await conn.execute(_UPSERT_CHANNEL, _channel_params(guild_id, record))


async def delete_channel(conn: Connection, channel_id: int) -> None:
    """Delete a channel or thread, and with it every stored message and chunk from it."""
    await conn.execute("DELETE FROM channels WHERE id = %s", (channel_id,))


async def load_guild_state(conn: Connection, guild_id: int) -> GuildState | None:
    """The stored state as the permission engine's input.

    None if the guild isn't stored or the bot has been removed from it.
    """
    cur = await conn.execute(
        "SELECT owner_id FROM guilds WHERE id = %s AND removed_at IS NULL", (guild_id,)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    (owner_id,) = row

    # No WHERE guild_id: row-level security already limits these to this guild.
    roles = await (await conn.execute("SELECT id, permissions FROM roles")).fetchall()
    rows = await (
        await conn.execute("SELECT id, kind, parent_id, overwrites FROM channels")
    ).fetchall()
    channels = {}
    for channel_id, kind, parent_id, overwrites in rows:
        try:
            channel_type = ChannelType(kind)
        except ValueError:
            continue
        channels[channel_id] = Channel(
            channel_id, channel_type, parent_id, tuple(_overwrite(o) for o in overwrites)
        )
    return GuildState(guild_id, owner_id, dict(roles), channels)


def _channel_params(guild_id: int, record: ChannelRecord) -> tuple[Any, ...]:
    overwrites = [
        {"id": o.id, "type": int(o.type), "allow": o.allow, "deny": o.deny}
        for o in record.overwrites
    ]
    return (
        record.id,
        guild_id,
        int(record.kind),
        record.name,
        record.parent_id,
        Jsonb(overwrites),
    )


def _overwrite(data: dict[str, int]) -> Overwrite:
    return Overwrite(data["id"], OverwriteType(data["type"]), data["allow"], data["deny"])
