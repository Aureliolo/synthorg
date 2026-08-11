"""Autonomy levels and autonomy comparison."""

from enum import StrEnum
from types import MappingProxyType


class AutonomyLevel(StrEnum):
    """Autonomy level controlling approval routing for agents.

    Determines which actions an agent can execute autonomously vs.
    which require human or security-agent approval (see
    ``docs/design/security.md``).
    """

    FULL = "full"
    SEMI = "semi"
    SUPERVISED = "supervised"
    LOCKED = "locked"


# Ordering: LOCKED (most restrictive) < SUPERVISED < SEMI < FULL (least restrictive).
_AUTONOMY_RANK: MappingProxyType[AutonomyLevel, int] = MappingProxyType(
    {
        AutonomyLevel.LOCKED: 0,
        AutonomyLevel.SUPERVISED: 1,
        AutonomyLevel.SEMI: 2,
        AutonomyLevel.FULL: 3,
    }
)

# Fail loudly if the rank table drifts from the enum membership; a new member
# left out of the table would otherwise raise a deferred KeyError at the first
# comparison rather than at import. The symmetric difference names the offender.
if set(_AUTONOMY_RANK) != set(AutonomyLevel):
    _autonomy_drift = set(_AUTONOMY_RANK) ^ set(AutonomyLevel)
    _autonomy_msg = f"_AUTONOMY_RANK out of sync: {_autonomy_drift}"
    raise RuntimeError(_autonomy_msg)


_RANK_TO_AUTONOMY: MappingProxyType[int, AutonomyLevel] = MappingProxyType(
    {rank: level for level, rank in _AUTONOMY_RANK.items()}
)


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


def step_down_autonomy(level: AutonomyLevel) -> AutonomyLevel:
    """Return the next-more-restrictive autonomy level (one step down).

    LOCKED is the floor: stepping down from LOCKED returns LOCKED.

    Args:
        level: The current autonomy level.

    Returns:
        The autonomy level one rank more restrictive, or LOCKED when
        already at the floor.
    """
    rank = _AUTONOMY_RANK[level]
    if rank == 0:
        return level
    return _RANK_TO_AUTONOMY[rank - 1]
