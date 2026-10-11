"""Runtime settings, read from the environment (and `.env` in development).

Secrets are `SecretStr` so they never show up in reprs, logs, or tracebacks.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    discord_token: SecretStr
    anthropic_api_key: SecretStr
    # SecretStr because the URL carries the DB password. Connects as the app role,
    # which is subject to row-level security.
    database_url: SecretStr
    log_level: LogLevel = "INFO"


class MigrationSettings(BaseSettings):
    """Only what Alembic needs, so migrations don't require the bot's tokens."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    # Connects as the schema owner role.
    migration_database_url: SecretStr


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once. Raises `pydantic.ValidationError` if any are missing."""
    return Settings()
