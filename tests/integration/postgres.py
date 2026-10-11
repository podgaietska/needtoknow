"""Shared constants and helpers for the Postgres integration tests."""

from dataclasses import dataclass
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

# Keep in sync with docker-compose.yml.
POSTGRES_IMAGE = "pgvector/pgvector:0.8.0-pg16"
ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_SQL = ROOT / "docker" / "initdb" / "01-roles.sql"

GUILD_A = 1001
GUILD_B = 2002

# Every tenant table and the column holding its guild ID. test_rls checks this
# matches the real schema, so a new table can't skip the isolation tests.
TENANT_COLUMNS = {
    "guilds": "id",
    "roles": "guild_id",
    "channels": "guild_id",
    "messages": "guild_id",
    "chunks": "guild_id",
    "opt_outs": "guild_id",
    "audit_log": "guild_id",
}

# One row per guild in every table. Run as the superuser, which bypasses RLS.
SEED_SQL = f"""
INSERT INTO guilds (id, owner_id) VALUES ({GUILD_A}, 9001), ({GUILD_B}, 9002);
INSERT INTO roles (id, guild_id, permissions) VALUES ({GUILD_A}, {GUILD_A}, 1024),
                                                     ({GUILD_B}, {GUILD_B}, 1024);
INSERT INTO channels (id, guild_id, kind, name) VALUES (1101, {GUILD_A}, 0, 'general'),
                                                       (2201, {GUILD_B}, 0, 'general');
INSERT INTO messages (id, guild_id, channel_id, author_id, content, created_at)
VALUES (1201, {GUILD_A}, 1101, 9001, 'CANARY-A', now()),
       (2301, {GUILD_B}, 2201, 9002, 'CANARY-B', now());
INSERT INTO chunks (guild_id, channel_id, message_ids, first_at, last_at, text, embedding)
VALUES ({GUILD_A}, 1101, '{{1201}}', now(), now(), 'CANARY-A',
        array_fill(0.1::real, ARRAY[384])::vector),
       ({GUILD_B}, 2201, '{{2301}}', now(), now(), 'CANARY-B',
        array_fill(0.1::real, ARRAY[384])::vector);
INSERT INTO opt_outs (guild_id, user_id) VALUES ({GUILD_A}, 9001), ({GUILD_B}, 9002);
INSERT INTO audit_log (guild_id, user_id, interaction_id, role_ids, visible_channels,
                       oracle_ok, retrieved, row_hash)
VALUES ({GUILD_A}, 9001, 1, '{{}}', '{{1101}}', true, '[]', 'hash-a'),
       ({GUILD_B}, 9002, 2, '{{}}', '{{2201}}', true, '[]', 'hash-b');
"""


@dataclass(frozen=True)
class Database:
    host: str
    port: int

    def url(self, user: str, password: str, dbname: str = "needtoknow") -> str:
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{dbname}"

    @property
    def superuser_url(self) -> str:
        return self.url("postgres", "postgres")

    @property
    def owner_url(self) -> str:
        return self.url("needtoknow_owner", "needtoknow_owner")

    @property
    def app_url(self) -> str:
        return self.url("needtoknow_app", "needtoknow_app")


def bootstrap(superuser_url: str) -> None:
    with psycopg.connect(superuser_url, autocommit=True) as conn:
        conn.execute(BOOTSTRAP_SQL.read_text())  # pyright: ignore[reportCallIssue, reportArgumentType]


def alembic_config(url: str) -> Config:
    config = Config(ROOT / "alembic.ini")
    config.attributes["url"] = url
    return config


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str) -> None:
    command.downgrade(alembic_config(url), revision)
