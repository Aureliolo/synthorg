# module-kind: code
"""Which step a hiring request in a given status admits.

One place answers it, so a step that runs out of order is refused with a
message naming the status it is actually in rather than failing later on a
missing field.
"""

from synthorg.hr.enums import HiringDecision, HiringRequestStatus
from synthorg.hr.errors import (
    HiringApprovalRequiredError,
    HiringError,
    HiringRejectedError,
    InvalidCandidateError,
)
from synthorg.hr.models import HiringRequest
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_HIRING_REQUEST_INVALID

logger = get_logger(__name__)


def _refuse(request: HiringRequest, msg: str) -> None:
    """Log a refused step against its request.

    Operation-neutral on purpose: both validators reach here, and a decision
    refused before any agent is built is not an instantiation failure. Naming
    it one records the wrong failed operation against a repeated approval.

    Args:
        request: The request the step was attempted on.
        msg: The refusal, which the caller then raises.
    """
    logger.warning(
        HR_HIRING_REQUEST_INVALID,
        request_id=str(request.id),
        error=msg,
    )


def validate_decidable(request: HiringRequest, *, decision: HiringDecision) -> None:
    """Refuse a decision on a request that is not open to it.

    PENDING admits either decision. APPROVED additionally admits a rejection,
    which is a WITHDRAWAL: the hire was authorised and no agent was ever built
    from it, so the operator is still the one who decides whether it happens,
    and nothing has been done that a rejection would have to undo. Refusing
    that hop is what left a live deployment holding a request reading approved
    behind an approval its operator had rejected, retried every sweep for
    seven days by a pass that could never complete it.

    INSTANTIATED admits neither: the agent is on the roster, and removing one
    is firing, which is its own decision with its own approval. REJECTED
    admits neither either, because re-approving a refusal silently is the same
    override in the other direction; a changed mind opens a fresh request.

    Args:
        request: The request being decided.
        decision: The decision being attempted, for the message.

    Raises:
        HiringError: If the request is not open to *decision*.
    """
    if request.status is HiringRequestStatus.PENDING:
        return
    if (
        request.status is HiringRequestStatus.APPROVED
        and decision is HiringDecision.REJECT
    ):
        return
    msg = (
        f"Cannot {decision} hiring request {request.id!r}: it is "
        f"{request.status.value}, not awaiting a decision"
    )
    _refuse(request, msg)
    raise HiringError(msg)


def validate_instantiable(request: HiringRequest) -> None:
    """Refuse instantiation of a request not cleared for it.

    Args:
        request: The hiring request to validate.

    Raises:
        HiringError: If already instantiated.
        HiringRejectedError: If request was rejected.
        HiringApprovalRequiredError: If request needs approval.
        InvalidCandidateError: If no candidate selected.
    """
    if request.status == HiringRequestStatus.INSTANTIATED:
        msg = f"Hiring request {request.id!r} is already instantiated"
        _refuse(request, msg)
        raise HiringError(msg)
    if request.status == HiringRequestStatus.REJECTED:
        msg = f"Hiring request {request.id!r} was rejected"
        _refuse(request, msg)
        raise HiringRejectedError(msg)
    if request.status == HiringRequestStatus.PENDING:
        msg = f"Hiring request {request.id!r} requires approval"
        _refuse(request, msg)
        raise HiringApprovalRequiredError(msg)
    if request.selected_candidate_id is None:
        msg = f"No candidate selected on request {request.id!r}"
        _refuse(request, msg)
        raise InvalidCandidateError(msg)


__all__ = ["validate_decidable", "validate_instantiable"]
