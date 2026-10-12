import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import discord
import pytest

from needtoknow.__main__ import main
from needtoknow.bot import NeedToKnowBot
from needtoknow.config import get_settings

ENV = {
    "DISCORD_TOKEN": "discord-secret",
    "ANTHROPIC_API_KEY": "anthropic-secret",
    "DATABASE_URL": "postgresql://u:db-secret@localhost/ntk",
    "DEV_GUILD_ID": "1001",
}


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    # No real .env, no cached settings, logging restored afterwards.
    monkeypatch.chdir(tmp_path)
    for key in [*ENV, "LOG_LEVEL"]:
        monkeypatch.delenv(key, raising=False)
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    root.handlers[:], root.level = saved


def test_runs_the_bot_with_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    calls: list[tuple[NeedToKnowBot, str, Any]] = []

    def fake_run(self: NeedToKnowBot, token: str, *, log_handler: Any) -> None:
        calls.append((self, token, log_handler))

    monkeypatch.setattr(NeedToKnowBot, "run", fake_run)

    main()

    ((bot, token, log_handler),) = calls
    assert token == "discord-secret"
    assert log_handler is None
    assert bot.dev_guild_id == 1001


def test_missing_settings_exit_with_names_not_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "discord-secret")

    with pytest.raises(SystemExit) as exit_info:
        main()

    message = str(exit_info.value.code)
    assert "ANTHROPIC_API_KEY" in message
    assert "DATABASE_URL" in message
    assert "DISCORD_TOKEN" not in message
    assert "discord-secret" not in message


@pytest.mark.parametrize(
    ("error", "hint"),
    [
        (discord.LoginFailure("Improper token has been passed."), "DISCORD_TOKEN"),
        (discord.PrivilegedIntentsRequired(shard_id=None), "Privileged Gateway Intents"),
    ],
)
def test_common_setup_errors_exit_with_a_hint(
    monkeypatch: pytest.MonkeyPatch, error: Exception, hint: str
) -> None:
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)

    def fail(self: NeedToKnowBot, token: str, *, log_handler: Any) -> None:
        raise error

    monkeypatch.setattr(NeedToKnowBot, "run", fail)

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert hint in str(exit_info.value.code)
