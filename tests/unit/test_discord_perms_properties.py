"""Property tests: the permission calculation agrees with a naive reference.

The reference is written differently on purpose. Instead of combining bitmasks,
it decides each bit on its own by walking the precedence order from strongest to
weakest. A mistake would have to be made the same way twice to go unnoticed.
"""

from functools import reduce
from operator import or_

from hypothesis import given, settings
from hypothesis import strategies as st

from needtoknow.authz.discord_perms import (
    ADMINISTRATOR,
    ALL_PERMISSIONS,
    READ_MESSAGE_HISTORY,
    VIEW_CHANNEL,
    Channel,
    ChannelType,
    GuildState,
    Member,
    Overwrite,
    OverwriteType,
    can_read,
    channel_permissions,
)

GUILD = 1
ROLE_IDS = [100, 101, 102]
UNKNOWN_ROLE = 999
USER_IDS = [10, 11, 12]
NOBODY = 99  # an owner who is none of the generated members
CHANNEL = 200
SEND_MESSAGES = 1 << 11
CHANNEL_BITS = [VIEW_CHANNEL, READ_MESSAGE_HISTORY, SEND_MESSAGES]


def bitmask(bits: list[int]) -> st.SearchStrategy[int]:
    return st.sets(st.sampled_from(bits)).map(lambda chosen: reduce(or_, chosen, 0))


channel_bits = bitmask(CHANNEL_BITS)
# Administrator is rarer than the others, so most examples exercise overwrites.
role_bits = st.one_of(
    channel_bits, channel_bits, channel_bits, bitmask([*CHANNEL_BITS, ADMINISTRATOR])
)

TARGETS = [
    (OverwriteType.ROLE, GUILD),
    *[(OverwriteType.ROLE, r) for r in ROLE_IDS],
    *[(OverwriteType.MEMBER, u) for u in USER_IDS],
]


@st.composite
def guild_states(draw: st.DrawFn) -> GuildState:
    roles = {GUILD: draw(role_bits)}
    for role_id in draw(st.sets(st.sampled_from(ROLE_IDS))):
        roles[role_id] = draw(role_bits)
    overwrites = draw(
        st.lists(
            st.tuples(st.sampled_from(TARGETS), channel_bits, channel_bits),
            unique_by=lambda t: t[0],
            max_size=len(TARGETS),
        )
    )
    return GuildState(
        guild_id=GUILD,
        owner_id=draw(st.sampled_from([*USER_IDS, NOBODY])),
        roles=roles,
        channels={
            CHANNEL: Channel(
                CHANNEL,
                ChannelType.GUILD_TEXT,
                overwrites=tuple(
                    Overwrite(target_id, kind, allow, deny)
                    for (kind, target_id), allow, deny in overwrites
                ),
            )
        },
    )


members = st.builds(
    Member,
    user_id=st.sampled_from(USER_IDS),
    role_ids=st.frozensets(st.sampled_from([*ROLE_IDS, GUILD, UNKNOWN_ROLE])),
)


def reference_permissions(guild: GuildState, member: Member, channel_id: int) -> int:
    """Discord's rules, one bit at a time, strongest rule first."""
    if member.user_id == guild.owner_id:
        return ALL_PERMISSIONS
    role_ids = member.role_ids | {GUILD}
    if any(guild.roles.get(r, 0) & ADMINISTRATOR for r in role_ids):
        return ALL_PERMISSIONS

    overwrites = {(o.type, o.id): o for o in guild.channels[channel_id].overwrites}
    own = overwrites.get((OverwriteType.MEMBER, member.user_id))
    role_overwrites = [
        overwrites[(OverwriteType.ROLE, r)]
        for r in member.role_ids - {GUILD}
        if (OverwriteType.ROLE, r) in overwrites
    ]
    everyone = overwrites.get((OverwriteType.ROLE, GUILD))

    result = 0
    for bit in (1 << i for i in range(64)):
        if own is not None and own.allow & bit:
            granted = True
        elif own is not None and own.deny & bit:
            granted = False
        elif any(o.allow & bit for o in role_overwrites):
            granted = True
        elif any(o.deny & bit for o in role_overwrites):
            granted = False
        elif everyone is not None and everyone.allow & bit:
            granted = True
        elif everyone is not None and everyone.deny & bit:
            granted = False
        else:
            granted = any(guild.roles.get(r, 0) & bit for r in role_ids)
        if granted:
            result |= bit
    return result


@settings(max_examples=2000)
@given(guild_states(), members)
def test_matches_reference(guild: GuildState, member: Member) -> None:
    assert channel_permissions(guild, member, CHANNEL) == reference_permissions(
        guild, member, CHANNEL
    )


@given(guild_states(), members)
def test_read_needs_view_and_history_in_the_result(guild: GuildState, member: Member) -> None:
    permissions = channel_permissions(guild, member, CHANNEL)

    expected = bool(permissions & VIEW_CHANNEL) and bool(permissions & READ_MESSAGE_HISTORY)
    assert can_read(permissions) is expected


@given(guild_states(), members)
def test_owner_always_has_everything(guild: GuildState, member: Member) -> None:
    owner = Member(guild.owner_id, member.role_ids)

    assert channel_permissions(guild, owner, CHANNEL) == ALL_PERMISSIONS
