from needtoknow.discord_state.health import GatewayHealth

GUILD, OTHER = 1, 2


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def synced_health(clock: FakeClock | None = None) -> GatewayHealth:
    health = GatewayHealth(max_disconnect_seconds=30, clock=clock or FakeClock())
    health.session_started()
    health.guild_synced(GUILD, health.session)
    return health


def test_nothing_is_current_before_connecting() -> None:
    assert not GatewayHealth().is_current(GUILD)


def test_connected_but_not_yet_synced() -> None:
    health = GatewayHealth()
    health.session_started()

    assert not health.is_current(GUILD)


def test_current_after_snapshot() -> None:
    health = synced_health()

    assert health.is_current(GUILD)
    assert not health.is_current(OTHER)


def test_short_disconnect_is_tolerated() -> None:
    clock = FakeClock()
    health = synced_health(clock)

    health.disconnected()
    clock.now += 30

    assert health.is_current(GUILD)


def test_long_disconnect_is_stale() -> None:
    clock = FakeClock()
    health = synced_health(clock)

    health.disconnected()
    clock.now += 31

    assert not health.is_current(GUILD)


def test_resume_restores_trust() -> None:
    clock = FakeClock()
    health = synced_health(clock)
    health.disconnected()
    clock.now += 300

    health.resumed()

    assert health.is_current(GUILD)


def test_repeated_disconnects_keep_the_first_time() -> None:
    clock = FakeClock()
    health = synced_health(clock)

    health.disconnected()
    clock.now += 20
    health.disconnected()
    clock.now += 20

    assert not health.is_current(GUILD)


def test_new_session_needs_a_new_snapshot() -> None:
    health = synced_health()

    health.session_started()
    assert not health.is_current(GUILD)

    health.guild_synced(GUILD, health.session)
    assert health.is_current(GUILD)


def test_snapshot_from_an_older_session_does_not_count() -> None:
    health = GatewayHealth()
    health.session_started()
    old_session = health.session

    health.session_started()  # reconnected while the old snapshot was being saved
    health.guild_synced(GUILD, old_session)

    assert not health.is_current(GUILD)


def test_failed_write_makes_the_guild_stale() -> None:
    health = synced_health()
    health.guild_synced(OTHER, health.session)

    health.guild_stale(GUILD)

    assert not health.is_current(GUILD)
    assert health.is_current(OTHER)
