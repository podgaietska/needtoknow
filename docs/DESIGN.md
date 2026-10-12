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
- [Channel permissions](#channel-permissions)
  - [Why not store who can read each chunk](#why-not-store-who-can-read-each-chunk)
  - [Discord's algorithm](#discords-algorithm)
  - [Threads, forums, and categories](#threads-forums-and-categories)
  - [Checking our math against Discord](#checking-our-math-against-discord)
  - [How we know it's right](#how-we-know-its-right)
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

## Channel permissions

Row-level security answers *which server*. Inside a server, a member may read
some channels and not others. The `needtoknow.authz` package answers *which
channels*, using Discord's own permission rules. It is pure code (no Discord
client, no database), so every rule is tested directly. A test fails if any
module in `authz/` imports Discord or database code.

### Why not store who can read each chunk

The obvious design copies an "allowed roles" list onto every stored chunk. It
breaks in two ways:

- **It can't express Discord's rules.** A channel can deny `@everyone`, allow a
  role, then deny one specific member of that role. "Allowed roles" can't say
  that.
- **It goes stale.** Who can read `#mod-chat` changes whenever someone gains or
  loses a role or a channel's settings change. Every change would mean rewriting
  every affected chunk, and until that finished, the old permissions would apply.

Instead, content only records *where* it came from (guild and channel), and
*who can read where* is computed when it's needed:

```python
visible_channels(guild_state, member) -> frozenset[int]   # channel IDs readable right now
```

Nothing about permissions is stored with content, so there's nothing to update
when they change. Losing a role takes effect on the very next call.

### Discord's algorithm

Discord permissions are bit flags: one integer per role or overwrite, one bit per
ability. Two bits decide reading: **View Channel** and **Read Message History**.
Both must survive the calculation.

Each channel can have **overwrites**: "in this channel, for this role (or this
one member), force these bits off (deny) or on (allow)". `channel_permissions()`
in `authz/discord_perms.py` follows Discord's documented order:

1. **Owner or administrator?** The server owner, or anyone whose roles include
   Administrator, gets every permission. Overwrites can't take anything away.
2. **Server-wide permissions:** `@everyone`'s bits combined with the bits of
   every role the member has. (`@everyone` is a role whose ID is the server's
   ID. It always applies, whether or not it's in the member's role list.)
3. **The channel's `@everyone` overwrite:** remove its denies, add its allows.
4. **The member's role overwrites, together:** remove all of their denies, then
   add all of their allows. So if one of a member's roles allows a bit and
   another denies it, **allow wins**.
5. **An overwrite for this specific member**, if any. It beats everything above.

Worked example: `#mod-chat` denies View Channel to `@everyone` and allows it to
`@Moderator`.

| Step | Alice (`@Member`) | Bob (`@Member`, `@Moderator`) |
|---|---|---|
| Server-wide | view ✓ | view ✓ |
| `@everyone` overwrite: deny view | view ✗ | view ✗ |
| Role overwrites | none for `@Member`: view ✗ | `@Moderator` allows view: view ✓ |
| Member overwrite | none | none |
| **Can read?** | **no** | **yes** |

A role ID that isn't in the server state grants nothing.

### Threads, forums, and categories

`authz/visibility.py` decides which channel's permissions govern a piece of
content (`permission_channel_id()`):

| Where the message is | Governed by |
|---|---|
| Text, announcement, or forum channel | Itself |
| Public thread, announcement thread, forum post | Its parent channel |
| Private thread | Nothing: never readable through the bot (it also needs thread membership, which isn't tracked) |
| Voice channel, category, unknown channel | Nothing |

Threads have no overwrites of their own, so a thread's permissions are its
parent's. **Categories are never consulted:** when a channel is synced with its
category, Discord copies the category's overwrites onto the channel, so the
channel's own list is complete.

`visible_channels()` lists only text, announcement, and forum channels. Content
in a thread is attributed to its parent, so a member can read a thread's content
exactly when they can read the parent.

### Checking our math against Discord

Every slash command interaction carries Discord's own computed permissions for
the member in the channel where the command was run. `oracle_agrees()` in
`authz/oracle.py` compares those with ours.

It compares only what reading depends on, the View Channel bit and the
"can read history" result, not the whole integer:

- For administrators, Discord sends every permission it currently defines, while
  we use all 64 bits. Those differ whenever Discord adds a permission.
- Discord applies extra implicit rules (for example, removing other channel
  permissions when View Channel is missing). They never change whether someone
  can read, so they're not modelled.

### How we know it's right

- **Table tests**, one row per rule or edge case, including:
  - `@everyone` deny plus role allow;
  - one role's allow beating another role's deny;
  - a role deny beating the `@everyone` overwrite's allow;
  - member allow and deny overriding roles;
  - an overwrite that allows and denies the same bit;
  - View Channel without Read Message History;
  - unknown roles;
  - Administrator and owner bypass;
  - threads and categories.
- **The `@everyone` trap:** interaction payloads may or may not include
  `@everyone` in a member's roles. If it were treated as an ordinary role, its
  overwrite would be applied twice and could undo another role's deny. One test
  is dedicated to this.
- **A second implementation.** A deliberately naive reference, written in the
  test suite, decides each bit separately by walking the rules from strongest to
  weakest. Hypothesis generates 2,000 random servers (roles, overwrites for roles
  and members, owners, unknown roles) and checks the two always agree.
- **The tests catch real bugs.** Applying `@everyone` twice, letting a role deny
  beat a role allow, or ignoring a member's allow each make the tests fail.

---

## Data model

All tables live in one migration-managed schema, and all are under the policy
above.

| Table | Holds |
|---|---|
| `guilds` | Each server: owner, when the bot joined or was removed |
| `roles` | Each role's server-wide permission bits |
| `channels` | Channels and threads: kind (Discord's channel type number), name, parent, permission overwrites, whether indexing is enabled |
| `messages` | Raw messages: author, content, reply target, timestamps |
| `chunks` | Searchable text with a 384-dimension embedding and the IDs of the messages it was built from |
| `opt_outs` | Users who asked not to be indexed |
| `audit_log` | One append-only row per question asked |

Deleting a guild cascades to its roles, channels, and opt-outs; deleting a
channel cascades to its messages and chunks, so purging is a single `DELETE`.
`audit_log` has no foreign keys, so audit rows outlive the channels they mention.
