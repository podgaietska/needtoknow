# needtoknow

> Answers questions from your server's history, on a need-to-know basis.

A Discord bot that answers questions from a server's message history, using only
the channels the person asking is allowed to see. Retrieval filters by the
caller's live Discord permissions before anything reaches the LLM, and an audit
log records what was retrieved for whom.

## How it works

needtoknow is a **multi-tenant, permission-aware RAG** (retrieval-augmented
generation) application. Each Discord server is a tenant. One bot and one
Postgres database serve them all, and Postgres row-level security guarantees one
server's content can never be returned to another, even if a query forgets to
filter.

See [docs/DESIGN.md](docs/DESIGN.md) for how it works.

## Development

Requires [uv](https://docs.astral.sh/uv/). It installs Python 3.12 if needed.

```bash
uv sync
cp .env.example .env   # then fill in the tokens
```

Local Postgres (with pgvector) runs in Docker. The first start creates two roles:
`needtoknow_owner` runs migrations, and `needtoknow_app` is what the bot uses.
Every table has row-level security keyed on the guild, and neither role can
bypass it.

```bash
docker compose up -d --wait
uv run alembic upgrade head
```

### Running the bot

1. In the [Discord Developer Portal](https://discord.com/developers/applications),
   create an application, open **Bot**, and copy its token into `DISCORD_TOKEN`.
2. On the same page, under **Privileged Gateway Intents**, enable
   **Message Content Intent**.
3. Under **OAuth2 → URL Generator**, pick the scopes `bot` and
   `applications.commands` and the permissions View Channels, Read Message
   History, and Send Messages. Open the URL to invite the bot to your server.
4. Optional: set `DEV_GUILD_ID` to your test server's ID (Discord: enable
   Developer Mode, then right-click the server → Copy Server ID). Commands then
   appear there immediately instead of after global registration.

```bash
uv run needtoknow
```

`/ping` in your server should answer `pong` with the gateway latency, visible
only to you.

### Checks

Checks (the same ones CI runs). The integration tests start their own Postgres
container, so they need Docker running:

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
```
