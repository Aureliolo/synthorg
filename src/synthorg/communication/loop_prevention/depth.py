"""Max delegation depth check (pure function)."""

from synthorg.communication.loop_prevention.models import GuardCheckOutcome
from synthorg.observability import get_logger
from synthorg.observability.events.delegation import (
    DELEGATION_LOOP_DEPTH_EXCEEDED,
)

logger = get_logger(__name__)

_MECHANISM = "max_depth"


def check_delegation_depth(
    delegation_chain: tuple[str, ...],
    max_depth: int,
) -> GuardCheckOutcome:
    """Check whether the delegation chain has reached or exceeded max depth.

    Args:
        delegation_chain: Current chain of delegator agent IDs.
        max_depth: Maximum allowed chain length (must be positive).

    Returns:
        Outcome with passed=True if within limit.

    Raises:
        ValueError: If ``max_depth`` is not positive.
    """
    if max_depth <= 0:
        msg = f"max_depth must be greater than 0, got {max_depth}"
        raise ValueError(msg)
    if len(delegation_chain) >= max_depth:
        logger.info(
            DELEGATION_LOOP_DEPTH_EXCEEDED,
            chain_length=len(delegation_chain),
            max_depth=max_depth,
        )
        return GuardCheckOutcome(
            passed=False,
            mechanism=_MECHANISM,
            message=(
                f"Delegation chain length {len(delegation_chain)} "
                f"reaches or exceeds max depth {max_depth}"
            ),
        )
    return GuardCheckOutcome(passed=True, mechanism=_MECHANISM)
