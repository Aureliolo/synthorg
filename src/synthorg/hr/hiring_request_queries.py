# module-kind: code
"""Questions the pipeline asks of the in-flight hiring set.

Reads only, over the map the service holds. Kept apart from the lifecycle
writes because each answer here is a rule about what a request MEANS (is a
hire already under way for this role, is a decision half-applied) rather than
a step in the flow, and those rules are the ones with a reason worth stating
next to them.
"""

from collections.abc import Mapping
from typing import Final

from synthorg.hr.enums import HiringRequestStatus
from synthorg.hr.models import HiringRequest

#: Statuses that mean a hire for a role is already under way. APPROVED
#: belongs here because approval and instantiation are separate steps, so a
#: request the operator said yes to is still an unanswered ask for an agent.
IN_FLIGHT_HIRING_STATUSES: Final[frozenset[HiringRequestStatus]] = frozenset(
    {HiringRequestStatus.PENDING, HiringRequestStatus.APPROVED}
)


def by_approval_id(
    requests: Mapping[str, HiringRequest], approval_id: str
) -> HiringRequest | None:
    """Find the in-flight request an approval item decides.

    Args:
        requests: The in-flight set.
        approval_id: The decided approval item's id.

    Returns:
        The request carrying that approval, or ``None`` when none does (every
        non-hiring approval lands here, and must read as a miss rather than an
        error).
    """
    return next(
        (r for r in requests.values() if r.approval_id == approval_id),
        None,
    )


def in_flight_for_role(
    requests: Mapping[str, HiringRequest], role: str
) -> HiringRequest | None:
    """Find a request for *role* that is still on its way to an agent.

    In flight is PENDING **or** APPROVED, not PENDING alone. Approval and
    instantiation are separate steps, so a request a human approved but that
    has not registered anybody yet is still the answer to "is a hire already
    under way for this role". Counting only PENDING would let a request stuck
    at APPROVED (one whose approval could propose no pair, say) open a fresh
    approval item and a fresh operator notification on every single pass,
    which is a queue full of duplicates asking for the same agent.

    A REJECTED request is deliberately NOT in flight: the operator answered,
    and a later gap is a new question rather than the one they declined.

    Args:
        requests: The in-flight set.
        role: The role name being staffed.

    Returns:
        The in-flight request, or ``None`` when no hire is under way.
    """
    return next(
        (
            r
            for r in requests.values()
            if r.status in IN_FLIGHT_HIRING_STATUSES and str(r.role) == role
        ),
        None,
    )


def approved_not_instantiated(
    requests: Mapping[str, HiringRequest],
) -> tuple[HiringRequest, ...]:
    """Return every request a human approved that has not been hired yet.

    Approval and instantiation are separate steps, so a failure between them
    (a request carrying no model binding, a registry outage) leaves an
    APPROVED request with no agent. The staffing sweep reads this to finish
    those rather than leaving the operator's decision half-applied.

    Args:
        requests: The in-flight set.

    Returns:
        The approved-but-not-instantiated requests, oldest first so a sweep
        applies decisions in the order they were made.
    """
    return tuple(
        sorted(
            (r for r in requests.values() if r.status is HiringRequestStatus.APPROVED),
            key=lambda r: r.created_at,
        )
    )


__all__ = [
    "IN_FLIGHT_HIRING_STATUSES",
    "approved_not_instantiated",
    "by_approval_id",
    "in_flight_for_role",
]
