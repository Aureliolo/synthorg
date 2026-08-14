# module-kind: code
"""Which step a hiring request in a given status admits.

One place answers it, so a step that runs out of order is refused with a
message naming the status it is actually in rather than failing later on a
missing field.
"""

from synthorg.hr.enums import HiringRequestStatus
from synthorg.hr.errors import (
    HiringApprovalRequiredError,
    HiringError,
    HiringRejectedError,
    InvalidCandidateError,
)
from synthorg.hr.models import HiringRequest
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_HIRING_INSTANTIATION_FAILED

logger = get_logger(__name__)


def _refuse(request: HiringRequest, msg: str) -> None:
    """Log a refused step against its request.

    Args:
        request: The request the step was attempted on.
        msg: The refusal, which the caller then raises.
    """
    logger.warning(
        HR_HIRING_INSTANTIATION_FAILED,
        request_id=str(request.id),
        error=msg,
    )


def validate_decidable(request: HiringRequest, *, decision: str) -> None:
    """Refuse a decision on a request that is not awaiting one.

    Args:
        request: The request being decided.
        decision: The decision being attempted, for the message.

    Raises:
        HiringError: If the request has already been decided or run.
    """
    if request.status is HiringRequestStatus.PENDING:
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
