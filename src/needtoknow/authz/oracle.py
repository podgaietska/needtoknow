"""Compare our permission calculation with Discord's own.

Every slash command interaction carries Discord's computed permissions for the
member in the channel where the command was run. Computing the same value from
our guild state and comparing the two checks our state and our math on real data.

Only the bits our decisions depend on are compared:

- Administrators: Discord sends every permission it defines, we use
  ALL_PERMISSIONS. Those differ whenever Discord adds a permission.
- Implicit rules: Discord removes other channel permissions when View Channel is
  missing, and limits members in timeout. We don't model those, so bits we never
  rely on can differ.

What must match is View Channel, and whether the member can read history.
"""

from needtoknow.authz.discord_perms import VIEW_CHANNEL, can_read


def oracle_agrees(ours: int, discords: int) -> bool:
    """Do our permissions and Discord's agree on everything reading depends on?"""
    same_view = (ours & VIEW_CHANNEL) == (discords & VIEW_CHANNEL)
    same_read = can_read(ours) == can_read(discords)
    return same_view and same_read
