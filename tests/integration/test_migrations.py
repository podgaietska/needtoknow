import psycopg

from .postgres import Database, bootstrap, downgrade, upgrade


def test_upgrade_downgrade_upgrade(database: Database) -> None:
    # A fresh database in the same container, so the seeded one isn't touched.
    # Also checks the bootstrap file is safe to re-run (the roles already exist).
    with psycopg.connect(database.superuser_url, autocommit=True) as conn:
        conn.execute("CREATE DATABASE roundtrip")
    bootstrap(database.url("postgres", "postgres", "roundtrip"))
    owner_url = database.url("needtoknow_owner", "needtoknow_owner", "roundtrip")

    upgrade(owner_url)
    downgrade(owner_url, "base")
    upgrade(owner_url)

    with psycopg.connect(owner_url) as conn:
        forced = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relforcerowsecurity"
        ).fetchone()

    assert forced == (7,)
