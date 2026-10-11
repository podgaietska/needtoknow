# needtoknow

> Answers questions from your server's history, on a need-to-know basis.

A Discord bot that answers questions from a server's message history, using only
the channels the person asking is allowed to see. Retrieval filters by the
caller's live Discord permissions before anything reaches the LLM, and an audit
log records what was retrieved for whom.

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

Checks (the same ones CI runs). The integration tests start their own Postgres
container, so they need Docker running:

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
```
