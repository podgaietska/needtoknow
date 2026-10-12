import pytest
from hypothesis import given
from hypothesis import strategies as st

from needtoknow.authz.discord_perms import (
    ADMINISTRATOR,
    ALL_PERMISSIONS,
    READ_MESSAGE_HISTORY,
    VIEW_CHANNEL,
)
from needtoknow.authz.oracle import oracle_agrees

READ = VIEW_CHANNEL | READ_MESSAGE_HISTORY
SEND_MESSAGES = 1 << 11
# What Discord might send for an administrator: every permission it defines today,
# which is not all 64 bits.
DISCORD_ADMIN = (1 << 51) - 1


@pytest.mark.parametrize(
    ("ours", "discords", "agree"),
    [
        (READ, READ, True),
        (0, 0, True),
        (READ | SEND_MESSAGES, READ, True),  # a bit we don't rely on differs
        (ALL_PERMISSIONS, DISCORD_ADMIN | ADMINISTRATOR, True),  # administrator
        # Discord drops other channel permissions when View Channel is missing; we don't.
        (READ_MESSAGE_HISTORY, 0, True),
        (READ, VIEW_CHANNEL, False),  # we'd allow reading, Discord wouldn't
        (VIEW_CHANNEL, READ, False),  # Discord would allow reading, we wouldn't
        (READ, READ_MESSAGE_HISTORY, False),  # we think they can see the channel
        (0, VIEW_CHANNEL, False),  # Discord thinks they can see the channel
    ],
)
def test_oracle(ours: int, discords: int, agree: bool) -> None:
    assert oracle_agrees(ours, discords) is agree


permissions = st.integers(min_value=0, max_value=ALL_PERMISSIONS)


@given(permissions)
def test_always_agrees_with_itself(value: int) -> None:
    assert oracle_agrees(value, value)


@given(permissions, permissions)
def test_symmetric(a: int, b: int) -> None:
    assert oracle_agrees(a, b) == oracle_agrees(b, a)
