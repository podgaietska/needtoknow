import json
import logging
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from needtoknow.logs import JsonFormatter, configure_logging


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root, discord = logging.getLogger(), logging.getLogger("discord")
    saved = root.handlers[:], root.level, discord.level
    yield
    root.handlers[:], root.level = saved[0], saved[1]
    discord.setLevel(saved[2])


def format_record(**kwargs: Any) -> dict[str, Any]:
    record = logging.LogRecord(
        "needtoknow.test", logging.INFO, __file__, 1, "hello %s", ("x",), None
    )
    record.__dict__.update(kwargs)
    result: dict[str, Any] = json.loads(JsonFormatter().format(record))
    return result


def test_formats_one_json_object() -> None:
    entry = format_record()

    assert entry["level"] == "INFO"
    assert entry["logger"] == "needtoknow.test"
    assert entry["msg"] == "hello x"
    assert entry["ts"].endswith("+00:00")


def test_includes_extra_fields() -> None:
    entry = format_record(guild_id=1001, user_id=9001)

    assert (entry["guild_id"], entry["user_id"]) == (1001, 9001)


def test_leaves_out_standard_record_attributes() -> None:
    entry = format_record()

    assert not {"args", "pathname", "lineno", "threadName"} & entry.keys()


def test_includes_exception_traceback() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        entry = format_record(exc_info=sys.exc_info())

    assert "RuntimeError: boom" in entry["exc"]


def test_configure_logging_writes_json_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")

    logging.getLogger("needtoknow.test").info("started", extra={"guild_id": 1001})

    entry = json.loads(capsys.readouterr().err)
    assert (entry["msg"], entry["guild_id"]) == ("started", 1001)


def test_discord_debug_logs_stay_off_at_debug_level() -> None:
    configure_logging("DEBUG")

    assert logging.getLogger("needtoknow").isEnabledFor(logging.DEBUG)
    assert not logging.getLogger("discord.gateway").isEnabledFor(logging.DEBUG)
    assert logging.getLogger("discord.gateway").isEnabledFor(logging.INFO)


def test_discord_follows_a_stricter_app_level() -> None:
    configure_logging("WARNING")

    assert not logging.getLogger("discord").isEnabledFor(logging.INFO)
