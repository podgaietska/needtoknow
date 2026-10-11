# How needtoknow works

needtoknow is a Discord bot that answers questions from a server's message
history, using only what the person asking is allowed to see. One bot and one
database serve many Discord servers, and every server's data stays its own.

This document explains how that works. Each section describes something that is
built and tested. Sections are added as more of the system lands.

- [Tenants](#tenants)
- [Connecting to Discord](#connecting-to-discord)
- [Tenant isolation with row-level security](#tenant-isolation-with-row-level-security)
  - [The usual way, and why it's fragile](#the-usual-way-and-why-its-fragile)
  - [What we do instead](#what-we-do-instead)
  - [Where the current guild comes from](#where-the-current-guild-comes-from)
  - [Closing the ways around RLS](#closing-the-ways-around-rls)
  - [How we know it holds](#how-we-know-it-holds)
- [Data model](#data-model)

---

## Tenants

A **tenant** is one Discord server, which Discord's API calls a **guild**. Every
row in every table records the guild it belongs to: a `guild_id` column (or, on
the `guilds` table itself, its `id`).

The guarantee: **content from one server can never be returned to another.**

Discord IDs (guilds, channels, messages, users) are snowflakes that are unique
across all of Discord, so plain `id` primary keys never collide between tenants.

---

## Connecting to Discord

The bot is one Python process (`uv run needtoknow`) built on discord.py. It
holds a websocket to Discord, the **gateway**, which pushes events to it as
they happen.

**Intents** choose which events the gateway sends. The bot asks only for what it
uses:

| Intent | Why |
|---|---|
| `guilds` | Servers, channels, roles, and threads |
| `guild_messages` | Messages created, edited, and deleted in servers |
| `message_content` | The text of those messages. Privileged: it must also be enabled for the application in the Discord Developer Portal, or Discord refuses the connection |

People talk to the bot through **slash commands**. Each one arrives as an
**interaction**, which Discord sends with the server it came from and the
member who ran it. On startup the bot uploads its command definitions to
Discord: to one server when `DEV_GUILD_ID` is set (they appear there
immediately), otherwise globally.

Three rules apply to every command:

- **Server only.** Commands can't be used in DMs. Every request belongs to a
  server, and so to exactly one tenant.
- **Private replies.** Replies are *ephemeral*: only the person who ran the
  command sees them.
- **No pings.** The client is created with `AllowedMentions.none()`, so nothing
  the bot sends can notify `@everyone`, a role, or a user, whatever the text
  contains.

**Logs carry IDs, not content.** Logs are JSON lines on stderr, with guild,
channel, and user IDs passed as fields. Message text never goes into them,
because whoever reads the logs can't necessarily see every channel. discord.py
logs raw gateway payloads (which include message text) at `DEBUG`, so its
loggers are held at `INFO` or above even when the app runs at `DEBUG`.

---

## Tenant isolation with row-level security

### The usual way, and why it's fragile

Most multi-tenant apps put a tenant ID on every row and rely on every query
including `WHERE guild_id = ?`. That works until someone writes one query
without it: a new purge job, a debug endpoint, a quick fix. That query quietly
returns every tenant's data, and nothing in the database knows anything is
wrong. Isolation is only as strong as the most careless query in the codebase.

### What we do instead

Postgres **row-level security (RLS)** attaches a rule to a table, and Postgres
adds that rule to *every* query against it. Every table has the same policy:

```sql
CREATE POLICY guild_isolation ON chunks
    USING      (guild_id = app_current_guild())   -- rows you can see, update, delete
    WITH CHECK (guild_id = app_current_guild());  -- rows you're allowed to write
```

So a query with no tenant filter at all:

```sql
SELECT text FROM chunks ORDER BY embedding <=> :question LIMIT 5;
```

is run by Postgres as if it said:

```sql
SELECT text FROM chunks WHERE guild_id = app_current_guild()
ORDER BY embedding <=> :question LIMIT 5;
```

The filter isn't in application code, so application code can't forget it.
Writes are checked too: inserting a row for another guild, or updating a row's
`guild_id` to another guild, is rejected.

### Where the current guild comes from

`app_current_guild()` reads a transaction-local setting, `app.guild_id`. In
application code, only `needtoknow.db.guild_scope()` sets it:

```python
async with guild_scope(pool, guild_id) as conn:  # BEGIN; app.guild_id = guild_id (local)
    await conn.execute("SELECT ... FROM chunks")  # only this guild's rows exist here
# COMMIT (or ROLLBACK on error); the setting ends with the transaction
```

Three details make this safe:

1. **The setting dies with the transaction.** It's set with
   `set_config('app.guild_id', $1, is_local => true)`, the parameterized form of
   `SET LOCAL`. When the connection goes back to the pool, the next borrower
   doesn't inherit the previous guild.
2. **No guild means no data.** Outside a scope, `app_current_guild()` is `NULL`.
   `guild_id = NULL` is never true, so reads return 0 rows and writes are
   rejected. Forgetting the scope fails closed.
3. **The empty-string trap.** Once a connection has used a transaction-local
   setting, Postgres resets it to `''`, not to "unset", and `''::bigint` is an
   error. The function treats both the same:

   ```sql
   CREATE FUNCTION app_current_guild() RETURNS bigint LANGUAGE sql STABLE
   AS $$ SELECT NULLIF(current_setting('app.guild_id', true), '')::bigint $$;
   ```

`guild_scope()` also rejects anything that isn't a positive integer (including
`True`, which Python treats as `1`). The pool runs in autocommit mode so each
scope is a real `BEGIN`/`COMMIT`, not a savepoint inside a longer transaction
that would keep the setting alive.

```
 request for guild G
        │
        ▼
 guild_scope(pool, G) ── BEGIN; set_config('app.guild_id', G, local)
        │
        ▼
 any SQL ──────────────► Postgres adds: guild_id = app_current_guild()  (= G)
        │
        ▼
 COMMIT / ROLLBACK ───── app.guild_id gone; connection returns to the pool clean
```

### Closing the ways around RLS

Turning RLS on isn't enough by itself. Postgres has three ways around it, and
each one is closed:

| Way around RLS | How it's closed |
|---|---|
| Table owners skip RLS by default | `FORCE ROW LEVEL SECURITY` on every table, so the owner is filtered too |
| Superusers and `BYPASSRLS` roles always skip it | Neither application role is a superuser or has `BYPASSRLS`. The superuser is used only for the one-time bootstrap |
| An owner can turn RLS off | The bot connects as a role that owns nothing. `ALTER TABLE … DISABLE ROW LEVEL SECURITY` is refused, and `SET row_security = off` raises an error instead of showing all rows |

That's why there are two database roles:

| Role | Used by | Can do |
|---|---|---|
| `needtoknow_owner` | Alembic migrations | Owns the schema. Still subject to RLS |
| `needtoknow_app` | The bot | Read and write rows it's granted, nothing else. On `audit_log`, only `SELECT` and `INSERT` |

The audit log is append-only for the bot: even a bug in the bot can't edit or
delete audit history.

Creating roles and the `vector` extension needs a superuser, so it happens once
in `docker/initdb/01-roles.sql` and never in a migration. Migrations run as the
owner (`MIGRATION_DATABASE_URL`); the bot runs as the app role (`DATABASE_URL`).

### How we know it holds

The integration tests run against a real Postgres + pgvector container, set up
with the same bootstrap file and migrations as development, and connect as the
app role, the same way the bot does. Both guilds are seeded with canary text
(`CANARY-A`, `CANARY-B`) in every table. The tests check:

- On every table, RLS is enabled and forced; a scope sees only its own guild;
  an unscoped connection sees nothing.
- A connection reused after a scoped transaction still sees nothing (the
  empty-string trap).
- Cross-guild inserts, updates, moves, and deletes are rejected or affect 0 rows.
- The audit log can't be updated or deleted.
- The app role can't disable RLS or turn `row_security` off.
- Neither role is a superuser or has `BYPASSRLS`.
- **Every table in the schema is in the test list.** A new table that skips RLS
  fails CI.

As a check on the tests themselves: changing the policy to `USING (true)` makes
23 of them fail.

---

## Data model

All tables live in one migration-managed schema, and all are under the policy
above.

| Table | Holds |
|---|---|
| `guilds` | Each server: owner, when the bot joined or was removed |
| `roles` | Each role's server-wide permission bits |
| `channels` | Channels and threads: kind, name, parent, permission overwrites, whether indexing is enabled |
| `messages` | Raw messages: author, content, reply target, timestamps |
| `chunks` | Searchable text with a 384-dimension embedding and the IDs of the messages it was built from |
| `opt_outs` | Users who asked not to be indexed |
| `audit_log` | One append-only row per question asked |

Deleting a guild cascades to its roles, channels, and opt-outs; deleting a
channel cascades to its messages and chunks, so purging is a single `DELETE`.
`audit_log` has no foreign keys, so audit rows outlive the channels they mention.
