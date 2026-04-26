"""Review-gate helpers for the approvals controller.

Extracted from ``approvals.py`` to keep that module under the 800-line
budget and to isolate the review-gate flow (mid-execution resume
vs review gate transition) from the controller CRUD logic.

Exposes:
- :func:`try_mid_execution_resume` -- resume parked context path.
- :func:`preflight_review_gate` -- pre-save self-review / task check.
- :func:`try_review_gate_transition` -- post-save IN_REVIEW transition.
- :func:`signal_resume_intent` -- orchestrates both flows.
"""

from typing import TYPE_CHECKING

from synthorg.api.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
)
from synthorg.engine.errors import (
    SelfReviewError,
    TaskInternalError,
    TaskNotFoundError,
    TaskVersionConflictError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_CONTEXT_LOADED,
    APPROVAL_GATE_RESUME_FAILED,
    APPROVAL_GATE_RESUME_TRIGGERED,
    APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
    APPROVAL_GATE_TASK_NOT_FOUND,
)
from synthorg.observability.events.security import (
    SECURITY_APPROVAL_SELF_REVIEW_PREVENTED,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.engine.review_gate import ReviewGateService

logger = get_logger(__name__)


async def try_mid_execution_resume(
    approval_gate: ApprovalGate,
    approval_id: str,
    *,
    approved: bool,
) -> bool:
    """Attempt to resume a mid-execution parked context.

    Returns ``True`` if the flow was handled (context found or
    error -- caller should not fall through to the review gate).
    Returns ``False`` if no parked context exists.
    """
    try:
        resumed = await approval_gate.resume_context(approval_id)
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            error="Failed to resume parked context",
            exc_info=True,
        )
        # Resume lookup failed -- do NOT fall through to review
        # gate, because the parked context may still exist.
        return True

    if resumed is not None:
        _context, parked_id = resumed
        logger.info(
            APPROVAL_GATE_RESUME_CONTEXT_LOADED,
            approval_id=approval_id,
            parked_id=parked_id,
            approved=approved,
            note=(
                "Parked context loaded -- agent re-execution "
                "requires external orchestration"
            ),
        )
        return True
    return False


async def preflight_review_gate(
    review_gate: ReviewGateService,
    approval_id: str,
    task_id: str,
    *,
    decided_by: str,
) -> None:
    """Run the review-gate preflight check before persisting a decision.

    Fails fast so that a rejected self-review attempt or a missing task
    never leaves a decided approval row or a broadcast WebSocket event
    behind.

    Raises:
        ForbiddenError: When the decider is the original executing
            agent (mapped from ``SelfReviewError``; a generic message
            is returned to avoid leaking internal identifiers).
        NotFoundError: When the task does not exist
            (mapped from ``TaskNotFoundError``; the client-facing
            message is generic to avoid leaking task UUIDs via 404).
        ServiceUnavailableError: When the task engine backend is
            unavailable (mapped from ``TaskInternalError``), mirroring
            the tasks controller's 503 handling for the same error.
    """
    try:
        await review_gate.check_can_decide(task_id=task_id, decided_by=decided_by)
    except SelfReviewError:
        logger.warning(
            SECURITY_APPROVAL_SELF_REVIEW_PREVENTED,
            approval_id=approval_id,
            task_id=task_id,
            decided_by=decided_by,
        )
        forbidden_msg = "Self-review is not permitted"
        raise ForbiddenError(forbidden_msg) from None
    except TaskNotFoundError as exc:
        logger.warning(
            APPROVAL_GATE_TASK_NOT_FOUND,
            approval_id=approval_id,
            task_id=task_id,
            decided_by=decided_by,
        )
        # Generic message: never echo the internal task_id to the
        # caller, since it could be used to enumerate valid task
        # identifiers via this endpoint.  The id is already in logs.
        not_found_msg = "Associated task could not be found"
        raise NotFoundError(not_found_msg) from exc
    except TaskInternalError as exc:
        logger.warning(
            APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
            approval_id=approval_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        unavailable_msg = "Internal server error"
        raise ServiceUnavailableError(unavailable_msg) from exc


async def try_review_gate_transition(  # noqa: PLR0913
    review_gate: ReviewGateService,
    approval_id: str,
    task_id: str,
    *,
    approved: bool,
    decided_by: str,
    decision_reason: str | None,
) -> None:
    """Delegate a review decision to the review gate service.

    Assumes ``preflight_review_gate`` has already validated self-review
    and task existence.  Surfaces engine-layer failures (task mutation,
    version conflict, persistence) as API errors so the caller sees a
    meaningful status code instead of a silent 200 OK with no state
    change.

    Raises:
        ConflictError: When the task disappears or its version
            conflicts between the preflight and the transition -- both
            treated as concurrent-modification races the client should
            retry.
        ForbiddenError: When a late self-review race is detected
            (agent reassigned between preflight and transition).
        ServiceUnavailableError: When the task engine backend becomes
            unavailable mid-transition.
    """
    try:
        await review_gate.complete_review(
            task_id=task_id,
            requested_by=decided_by,
            approved=approved,
            decided_by=decided_by,
            reason=decision_reason,
            approval_id=approval_id,
        )
    except SelfReviewError:
        logger.warning(
            SECURITY_APPROVAL_SELF_REVIEW_PREVENTED,
            approval_id=approval_id,
            task_id=task_id,
            decided_by=decided_by,
        )
        forbidden_msg = "Self-review is not permitted"
        raise ForbiddenError(forbidden_msg) from None
    except TaskNotFoundError as exc:
        logger.warning(
            APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
            approval_id=approval_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        # Generic message: do not echo task UUIDs to clients via 404.
        not_found_msg = "Associated task could not be found"
        raise NotFoundError(not_found_msg) from exc
    except TaskVersionConflictError as exc:
        logger.warning(
            APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
            approval_id=approval_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        # Generic message: do not echo task UUIDs to clients via 409.
        conflict_msg = "A concurrent modification was detected; retry the request"
        raise ConflictError(conflict_msg) from exc
    except TaskInternalError as exc:
        logger.warning(
            APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
            approval_id=approval_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        unavailable_msg = "Internal server error"
        raise ServiceUnavailableError(unavailable_msg) from exc


async def signal_resume_intent(  # noqa: PLR0913
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
    decided_by: str,
    decision_reason: str | None = None,
    task_id: str | None = None,
) -> None:
    """Execute the resume or review-gate flow for a decided approval.

    Two flows depending on whether a parked context exists:

    1. **Mid-execution parking** (:func:`try_mid_execution_resume`):
       resume a parked context if one exists.
    2. **Review gate** (:func:`try_review_gate_transition`):
       transition the task from IN_REVIEW on approval/rejection.

    Args:
        app_state: Application state containing services.
        approval_id: The approval item identifier.
        approved: Whether the action was approved.
        decided_by: Who made the decision.
        decision_reason: Optional reason for the decision.
        task_id: Optional task identifier for review-gate flow.
    """
    logger.info(
        APPROVAL_GATE_RESUME_TRIGGERED,
        approval_id=approval_id,
        approved=approved,
        decided_by=decided_by,
        has_reason=decision_reason is not None,
    )

    # Flow 1: mid-execution parking.
    approval_gate = app_state.approval_gate
    if approval_gate is not None:
        handled = await try_mid_execution_resume(
            approval_gate, approval_id, approved=approved
        )
        if handled:
            return

    # Flow 2: review gate -- transition task status.
    review_gate = app_state.review_gate_service
    if review_gate is not None and task_id is not None:
        await try_review_gate_transition(
            review_gate,
            approval_id,
            task_id,
            approved=approved,
            decided_by=decided_by,
            decision_reason=decision_reason,
        )
