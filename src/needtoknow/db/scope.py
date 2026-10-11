"""Guild-scoped database access.

Every table is under Postgres row-level security keyed on the guild ID in the
transaction-local `app.guild_id` setting. `guild_scope()` is the only way to set
it. A connection used outside a scope reads nothing and can't write.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

type Pool = AsyncConnectionPool[AsyncConnection[TupleRow]]


def create_pool(conninfo: str, *, min_size: int = 1, max_size: int = 10) -> Pool:
    """Create a pool. Open it with `await pool.open()` or `async with pool`."""
    # autocommit, so each `conn.transaction()` is a real BEGIN/COMMIT and the
    # local guild setting ends with it.
    return AsyncConnectionPool(
        conninfo,
        min_size=min_size,
        max_size=max_size,
        kwargs={"autocommit": True},
        open=False,
    )


@asynccontextmanager
async def guild_scope(pool: Pool, guild_id: int) -> AsyncIterator[AsyncConnection[TupleRow]]:
    """Yield a connection inside a transaction that can only see `guild_id`'s rows.

    The transaction commits when the block exits normally and rolls back on error.
    """
    if isinstance(guild_id, bool) or not isinstance(guild_id, int) or guild_id <= 0:
        raise ValueError(f"invalid guild_id: {guild_id!r}")

    async with pool.connection() as conn, conn.transaction():
        # set_config(..., is_local => true) is SET LOCAL that accepts a parameter.
        await conn.execute("SELECT set_config('app.guild_id', %s, true)", (str(guild_id),))
        yield conn
