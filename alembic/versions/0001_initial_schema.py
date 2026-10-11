"""Initial schema with per-guild row-level security.

Every table is tenant data keyed on a guild. Row-level security is enabled and
FORCEd on all of them, so even the owner role only sees the guild named by the
transaction-local `app.guild_id` setting. With no guild set, reads return no rows
and writes are rejected.

Revision ID: 0001
Revises:
Create Date: 2026-10-11
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "needtoknow_app"

# table -> the column holding its guild ID
TENANT_TABLES = {
    "guilds": "id",
    "roles": "guild_id",
    "channels": "guild_id",
    "messages": "guild_id",
    "chunks": "guild_id",
    "opt_outs": "guild_id",
    "audit_log": "guild_id",
}

# The audit log is append-only for the app.
APP_GRANTS = {table: "SELECT, INSERT, UPDATE, DELETE" for table in TENANT_TABLES}
APP_GRANTS["audit_log"] = "SELECT, INSERT"


def upgrade() -> None:
    # NULLIF: once a connection has run set_config(..., is_local => true), the
    # setting reverts to '' (not NULL) after the transaction, and ''::bigint errors.
    op.execute("""
        CREATE FUNCTION app_current_guild() RETURNS bigint
        LANGUAGE sql STABLE
        AS $$ SELECT NULLIF(current_setting('app.guild_id', true), '')::bigint $$
    """)

    # Mirrored Discord state, kept current by gateway events.
    op.execute("""
        CREATE TABLE guilds (
            id          bigint PRIMARY KEY,
            owner_id    bigint NOT NULL,
            joined_at   timestamptz,
            removed_at  timestamptz
        )
    """)
    op.execute("""
        CREATE TABLE roles (
            id           bigint PRIMARY KEY,
            guild_id     bigint NOT NULL REFERENCES guilds (id) ON DELETE CASCADE,
            permissions  bigint NOT NULL
        )
    """)
    op.execute("CREATE INDEX roles_guild_id_idx ON roles (guild_id)")
    op.execute("""
        CREATE TABLE channels (
            id                bigint PRIMARY KEY,
            guild_id          bigint NOT NULL REFERENCES guilds (id) ON DELETE CASCADE,
            parent_id         bigint,  -- category, or the parent channel of a thread
            kind              smallint NOT NULL,
            name              text NOT NULL,
            overwrites        jsonb NOT NULL DEFAULT '[]',  -- [{id, type, allow, deny}]
            indexing_enabled  boolean NOT NULL DEFAULT false,
            last_message_id   bigint
        )
    """)
    op.execute("CREATE INDEX channels_guild_id_idx ON channels (guild_id)")

    # Content.
    op.execute("""
        CREATE TABLE messages (
            id           bigint PRIMARY KEY,
            guild_id     bigint NOT NULL,
            channel_id   bigint NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
            author_id    bigint NOT NULL,
            author_name  text,
            content      text NOT NULL,
            reply_to     bigint,
            created_at   timestamptz NOT NULL,
            edited_at    timestamptz
        )
    """)
    op.execute("CREATE INDEX messages_channel_id_id_idx ON messages (channel_id, id)")
    op.execute("CREATE INDEX messages_guild_id_author_id_idx ON messages (guild_id, author_id)")

    op.execute("""
        CREATE TABLE chunks (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            guild_id     bigint NOT NULL,
            -- the channel whose permissions apply (a thread's parent)
            channel_id   bigint NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
            thread_id    bigint REFERENCES channels (id) ON DELETE CASCADE,
            message_ids  bigint[] NOT NULL,
            first_at     timestamptz NOT NULL,
            last_at      timestamptz NOT NULL,
            text         text NOT NULL,
            tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
            embedding    vector(384) NOT NULL
        )
    """)
    op.execute(
        "CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("CREATE INDEX chunks_tsv_idx ON chunks USING gin (tsv)")
    op.execute("CREATE INDEX chunks_message_ids_idx ON chunks USING gin (message_ids)")
    op.execute("CREATE INDEX chunks_guild_id_channel_id_idx ON chunks (guild_id, channel_id)")

    op.execute("""
        CREATE TABLE opt_outs (
            guild_id  bigint NOT NULL REFERENCES guilds (id) ON DELETE CASCADE,
            user_id   bigint NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
    """)

    # Append-only. No foreign key: audit rows outlive the channels they mention.
    op.execute("""
        CREATE TABLE audit_log (
            id                 bigserial PRIMARY KEY,
            guild_id           bigint NOT NULL,
            user_id            bigint NOT NULL,
            interaction_id     bigint NOT NULL,
            role_ids           bigint[] NOT NULL,
            visible_channels   bigint[] NOT NULL,
            oracle_ok          boolean NOT NULL,
            query_text         text,
            retrieved          jsonb NOT NULL,
            cited_message_ids  bigint[],
            postcheck_drops    int NOT NULL DEFAULT 0,
            model              text,
            prev_hash          text,
            row_hash           text NOT NULL,
            created_at         timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX audit_log_guild_id_id_idx ON audit_log (guild_id, id)")

    for table, column in TENANT_TABLES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY guild_isolation ON {table}
                USING ({column} = app_current_guild())
                WITH CHECK ({column} = app_current_guild())
        """)
        op.execute(f"GRANT {APP_GRANTS[table]} ON {table} TO {APP_ROLE}")
    op.execute(f"GRANT USAGE ON SEQUENCE audit_log_id_seq TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION app_current_guild()")
