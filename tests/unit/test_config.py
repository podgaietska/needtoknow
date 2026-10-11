import pytest
from pydantic import ValidationError

from needtoknow.config import MigrationSettings, Settings

ENV = {
    "DISCORD_TOKEN": "discord-secret",
    "ANTHROPIC_API_KEY": "anthropic-secret",
    "DATABASE_URL": "postgresql://u:db-secret@localhost/ntk",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


@pytest.mark.usefixtures("env")
def test_loads_from_environment() -> None:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert settings.discord_token.get_secret_value() == "discord-secret"
    assert settings.log_level == "INFO"


@pytest.mark.usefixtures("env")
def test_secrets_are_not_in_repr() -> None:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    text = repr(settings) + str(settings.model_dump())
    for secret in ("discord-secret", "anthropic-secret", "db-secret"):
        assert secret not in text


def test_missing_secrets_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


@pytest.mark.usefixtures("env")
def test_rejects_unknown_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "LOUD")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


@pytest.mark.usefixtures("env")
def test_dev_guild_id_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_GUILD_ID", "")
    assert Settings(_env_file=None).dev_guild_id is None  # pyright: ignore[reportCallIssue]

    monkeypatch.setenv("DEV_GUILD_ID", "1001")
    assert Settings(_env_file=None).dev_guild_id == 1001  # pyright: ignore[reportCallIssue]


@pytest.mark.usefixtures("env")
def test_empty_token_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_migration_settings_need_only_the_owner_url(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://owner:owner-secret@localhost/ntk")

    settings = MigrationSettings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert "owner-secret" not in repr(settings)
