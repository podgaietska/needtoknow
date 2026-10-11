"""Guild isolation is enforced by Postgres, as the app role, on every table."""

import psycopg
import pytest
from psycopg import sql

from needtoknow.db import Pool, create_pool, guild_scope

from .postgres import GUILD_A, GUILD_B, TENANT_COLUMNS, Database

TABLES = sorted(TENANT_COLUMNS)
NOT_TENANT_DATA = {"alembic_version"}


def test_every_table_is_covered(database: Database) -> None:
    with psycopg.connect(database.superuser_url) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()

    assert {name for (name,) in rows} - NOT_TENANT_DATA == set(TENANT_COLUMNS)


@pytest.mark.parametrize("table", TABLES)
def test_rls_is_enabled_and_forced(database: Database, table: str) -> None:
    with psycopg.connect(database.superuser_url) as conn:
        row = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (table,),
        ).fetchone()

    assert row == (True, True)


@pytest.mark.parametrize("role", ["needtoknow_app", "needtoknow_owner"])
def test_roles_cannot_bypass_rls(database: Database, role: str) -> None:
    with psycopg.connect(database.superuser_url) as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (role,)
        ).fetchone()

    assert row == (False, False)


def guild_ids_query(table: str) -> sql.Composed:
    return sql.SQL("SELECT DISTINCT {} FROM {}").format(
        sql.Identifier(TENANT_COLUMNS[table]), sql.Identifier(table)
    )


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("guild_id", [GUILD_A, GUILD_B])
async def test_scope_sees_only_its_own_guild(pool: Pool, table: str, guild_id: int) -> None:
    async with guild_scope(pool, guild_id) as conn:
        rows = await (await conn.execute(guild_ids_query(table))).fetchall()

    assert rows == [(guild_id,)]


@pytest.mark.parametrize("table", TABLES)
async def test_unscoped_connection_sees_nothing(pool: Pool, table: str) -> None:
    async with pool.connection() as conn:
        rows = await (await conn.execute(guild_ids_query(table))).fetchall()

    assert rows == []


async def test_scope_does_not_outlive_its_transaction(database: Database) -> None:
    # One connection, so the unscoped query reuses the one that was scoped.
    # Afterwards the setting is '' rather than unset, which must still mean "no guild".
    async with create_pool(database.app_url, min_size=1, max_size=1) as pool:
        async with guild_scope(pool, GUILD_A) as conn:
            await conn.execute("SELECT 1")
        async with pool.connection() as conn:
            rows = await (await conn.execute("SELECT id FROM guilds")).fetchall()

    assert rows == []


async def test_unscoped_insert_is_rejected(pool: Pool) -> None:
    async with pool.connection() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute(
                "INSERT INTO opt_outs (guild_id, user_id) VALUES (%s, 1)", (GUILD_A,)
            )


async def test_cannot_insert_into_another_guild(pool: Pool) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with guild_scope(pool, GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO opt_outs (guild_id, user_id) VALUES (%s, 1)", (GUILD_B,)
            )


async def test_cannot_move_a_row_to_another_guild(pool: Pool) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with guild_scope(pool, GUILD_A) as conn:
            await conn.execute("UPDATE channels SET guild_id = %s WHERE id = 1101", (GUILD_B,))


async def test_cannot_touch_another_guilds_rows(pool: Pool) -> None:
    async with guild_scope(pool, GUILD_A) as conn:
        updated = await conn.execute("UPDATE channels SET name = 'pwned' WHERE id = 2201")
        deleted = await conn.execute("DELETE FROM messages WHERE id = 2301")

    assert (updated.rowcount, deleted.rowcount) == (0, 0)


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_log SET row_hash = 'x'", "DELETE FROM audit_log"],
)
async def test_audit_log_is_append_only(pool: Pool, statement: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with guild_scope(pool, GUILD_A) as conn:
            await conn.execute(statement)


async def test_app_role_cannot_disable_rls(pool: Pool) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with guild_scope(pool, GUILD_A) as conn:
            await conn.execute("ALTER TABLE chunks DISABLE ROW LEVEL SECURITY")


async def test_app_role_cannot_turn_row_security_off(pool: Pool) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with guild_scope(pool, GUILD_A) as conn:
            await conn.execute("SET LOCAL row_security = off")
            await conn.execute("SELECT * FROM chunks")


@pytest.mark.parametrize("bad", [0, -1, True, "1001", None])
async def test_guild_scope_rejects_invalid_ids(pool: Pool, bad: object) -> None:
    with pytest.raises(ValueError, match="invalid guild_id"):
        async with guild_scope(pool, bad):  # type: ignore[arg-type]
            pass
