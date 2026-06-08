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
