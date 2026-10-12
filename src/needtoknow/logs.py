"""Structured JSON logging.

One JSON object per line on stderr. Log IDs (guild, channel, user, message), never
message text or question text: logs are read by people who can't see every channel.
Pass IDs as `extra`, e.g. `log.info("ask", extra={"guild_id": g, "user_id": u})`.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Attributes every LogRecord has. Anything else on a record came from `extra`.
_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}

# discord.py logs raw gateway payloads at DEBUG, and those include message content.
# Its loggers never go below INFO, whatever the app's level is.
DISCORD_MIN_LEVEL = logging.INFO


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RECORD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Send all logging (ours and discord.py's) to stderr as JSON lines."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    discord_level = max(logging.getLevelNamesMapping()[level], DISCORD_MIN_LEVEL)
    logging.getLogger("discord").setLevel(discord_level)
