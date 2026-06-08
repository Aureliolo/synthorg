"""Autonomy levels and autonomy comparison."""

from enum import StrEnum


class AutonomyLevel(StrEnum):
    """Autonomy level controlling approval routing for agents.

    Determines which actions an agent can execute autonomously vs.
    which require human or security-agent approval (see Operations design page).
    """

    FULL = "full"
    SEMI = "semi"
    SUPERVISED = "supervised"
    LOCKED = "locked"


# Ordering: LOCKED (most restrictive) < SUPERVISED < SEMI < FULL (least restrictive).
_AUTONOMY_RANK: dict[AutonomyLevel, int] = {
    AutonomyLevel.LOCKED: 0,
    AutonomyLevel.SUPERVISED: 1,
    AutonomyLevel.SEMI: 2,
    AutonomyLevel.FULL: 3,
}

# Fail loudly if the rank table drifts from the enum membership; a new member
# left out of the table would otherwise raise a deferred KeyError at the first
# comparison rather than at import. The symmetric difference names the offender.
if set(_AUTONOMY_RANK) != set(AutonomyLevel):
    _autonomy_drift = set(_AUTONOMY_RANK) ^ set(AutonomyLevel)
    _autonomy_msg = f"_AUTONOMY_RANK out of sync: {_autonomy_drift}"
    raise RuntimeError(_autonomy_msg)


def compare_autonomy(a: AutonomyLevel, b: AutonomyLevel) -> int:
    """Compare two autonomy levels.

    Returns negative if *a* is more restrictive than *b*, zero if equal,
    positive if *a* is less restrictive than *b*.

    Args:
        a: First autonomy level.
        b: Second autonomy level.

    Returns:
        Integer indicating relative autonomy.
    """
    return _AUTONOMY_RANK[a] - _AUTONOMY_RANK[b]
