"""Run the bot: `uv run needtoknow` (or `python -m needtoknow`)."""

import sys

import discord
from psycopg_pool import PoolTimeout
from pydantic import ValidationError

from needtoknow.bot import NeedToKnowBot
from needtoknow.config import get_settings
from needtoknow.db import create_pool
from needtoknow.logs import configure_logging


def main() -> None:
    try:
        settings = get_settings()
    except ValidationError as error:
        # Name the bad settings, never their values.
        names = sorted({str(e["loc"][0]).upper() for e in error.errors()})
        sys.exit(f"Missing or invalid settings: {', '.join(names)}. See .env.example.")

    configure_logging(settings.log_level)
    pool = create_pool(settings.database_url.get_secret_value())
    bot = NeedToKnowBot(pool=pool, dev_guild_id=settings.dev_guild_id)
    try:
        # log_handler=None: logging is configured above; don't let discord.py add its own.
        bot.run(settings.discord_token.get_secret_value(), log_handler=None)
    except discord.LoginFailure:
        sys.exit("Discord rejected DISCORD_TOKEN. Copy the bot token from the Developer Portal.")
    except PoolTimeout:
        sys.exit("Couldn't connect to the database at DATABASE_URL. Is Postgres running?")
    except discord.PrivilegedIntentsRequired:
        sys.exit(
            "Discord refused the Message Content intent. Enable it in the Developer Portal "
            "under Bot > Privileged Gateway Intents."
        )


if __name__ == "__main__":
    main()
