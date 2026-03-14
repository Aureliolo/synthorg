"""Ancestry cycle detection check (pure function)."""

from synthorg.communication.loop_prevention.models import GuardCheckOutcome
from synthorg.observability import get_logger
from synthorg.observability.events.delegation import (
    DELEGATION_LOOP_ANCESTRY_BLOCKED,
)

logger = get_logger(__name__)

_MECHANISM = "ancestry"


def check_ancestry(
    delegation_chain: tuple[str, ...],
    delegatee_id: str,
) -> GuardCheckOutcome:
    """Check whether delegating to delegatee would create an ancestry cycle.

    Args:
        delegation_chain: Current chain of delegator agent IDs.
        delegatee_id: Agent ID of the proposed delegatee.

    Returns:
        Outcome with passed=False if delegatee is already in the chain.
    """
    if delegatee_id in delegation_chain:
        logger.info(
            DELEGATION_LOOP_ANCESTRY_BLOCKED,
            delegatee=delegatee_id,
            chain=delegation_chain,
        )
        return GuardCheckOutcome(
            passed=False,
            mechanism=_MECHANISM,
            message=(
                f"Agent {delegatee_id!r} is already in the "
                f"delegation chain: {delegation_chain}"
            ),
        )
    return GuardCheckOutcome(passed=True, mechanism=_MECHANISM)
