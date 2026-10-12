"""Can the stored permission state for a guild be trusted right now?

The stored state is a copy, kept current by gateway events. It can fall behind:

- **The gateway is down.** No events arrive. A short gap is fine: when the
  connection resumes, Discord replays the events that were missed.
- **A new session started.** Discord couldn't resume, so missed events are gone
  for good. Each guild is untrusted until its fresh snapshot has been saved.
- **Saving an event failed.** That guild's copy is missing a change.
"""

import time
from collections.abc import Callable

# How long a disconnect can last before the state counts as stale.
DEFAULT_MAX_DISCONNECT_SECONDS = 30.0


class GatewayHealth:
    def __init__(
        self,
        *,
        max_disconnect_seconds: float = DEFAULT_MAX_DISCONNECT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_disconnect = max_disconnect_seconds
        self._clock = clock
        self._session = 0
        self._connected = False
        self._disconnected_at: float | None = None
        self._synced: dict[int, int] = {}  # guild ID -> session its snapshot was saved in

    @property
    def session(self) -> int:
        """Changes on every new gateway session. Read it before saving a snapshot."""
        return self._session

    def session_started(self) -> None:
        """A new session: everything saved before it may be missing changes."""
        self._session += 1
        self._connected = True
        self._disconnected_at = None

    def resumed(self) -> None:
        """The old session continued; Discord replays the events we missed."""
        self._connected = True
        self._disconnected_at = None

    def disconnected(self) -> None:
        if self._connected:
            self._connected = False
            self._disconnected_at = self._clock()

    def guild_synced(self, guild_id: int, session: int) -> None:
        """A snapshot taken during `session` has been saved."""
        self._synced[guild_id] = session

    def guild_stale(self, guild_id: int) -> None:
        """The guild's stored state may be missing a change."""
        self._synced.pop(guild_id, None)

    def is_current(self, guild_id: int) -> bool:
        if not self._connected and (
            self._disconnected_at is None
            or self._clock() - self._disconnected_at > self._max_disconnect
        ):
            return False
        return self._synced.get(guild_id) == self._session
