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

from synthorg.core.actor_context import resolve_decided_by
from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
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
    APPROVAL_GATE_CONVERSATIONAL_EXECUTED,
    APPROVAL_GATE_CONVERSATIONAL_FAILED,
    APPROVAL_GATE_CONVERSATIONAL_NO_PROPOSAL,
    APPROVAL_GATE_CONVERSATIONAL_REJECTED,
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
    from synthorg.core.approval import ApprovalItem
    from synthorg.engine.review_gate import ReviewGateService

logger = get_logger(__name__)


async def _reread_approval_item(
    app_state: AppState,
    approval_id: str,
) -> ApprovalItem | None:
    """Re-read the just-decided approval, degrading to ``None`` on error.

    The decision is already persisted by the caller; a failed reread
    must not 500 the request. ``None`` is treated per flow:
    ``try_conversational_intake_resume`` (Flow 0) raises because that
    flow owns the approval the moment it reads the source, so a
    missing source read can't be safely fallen-through; later flows
    (mid-execution-resume, review-gate) degrade gracefully because
    they probe additional state to determine ownership.
    """
    try:
        return await app_state.approval_store.get(approval_id)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="approval reread failed; falling back to parked-context probe",
        )
        return None


async def try_mid_execution_resume(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
    decided_by: str,
    decision_reason: str | None,
) -> bool:
    """Dispatch a parked-context resume if one exists for this approval.

    Cheap non-destructive existence peek
    (:meth:`ApprovalGate.has_parked_context`) decides the flow without
    consuming the parked record or emitting the resume-started audit
    event. When a parked context exists the actual restore + agent
    re-run is delegated to the worker execution service, which spawns
    it as a tracked background task so the approve/reject HTTP response
    is not blocked by a full agent re-run (the decision is already
    persisted by the caller before this runs).

    Routing is deterministic off the approval's persisted
    :attr:`ApprovalItem.source` discriminator (fixed at creation), not
    a live parked-context probe: ``PARKED_CONTEXT`` means this flow
    owns the decision, anything else falls through to the review gate.
    The legacy ``has_parked_context`` probe is kept only as a logged
    fallback for the degenerate case where the just-decided approval
    cannot be re-read (it should always be present here, since the
    caller persisted the decision immediately before).

    Returns ``True`` when the mid-execution flow is responsible for
    this approval so the caller does not also run the review-gate
    transition. Returns ``False`` when the approval is review-gate
    bound (e.g. a hiring/promotion approval) so the caller falls
    through to the review gate.
    """
    from synthorg.core.enums import ApprovalSource  # noqa: PLC0415

    item = await _reread_approval_item(app_state, approval_id)
    if item is not None:
        # Deterministic primary path: the source was fixed when the
        # approval was created, so routing cannot flip on a transient
        # parked-context backend outage.
        if item.source is not ApprovalSource.PARKED_CONTEXT:
            return False
    else:
        # Fallback only: the decision was just persisted by the caller,
        # so a missing item is unexpected. Probe the gate to avoid
        # stranding a possibly-parked approval in the review gate.
        gate = app_state.approval_gate
        if gate is None:
            return False
        try:
            has_parked = await gate.has_parked_context(approval_id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                APPROVAL_GATE_RESUME_FAILED,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="approval item missing; parked-context probe failed",
            )
            # Indeterminate: a parked context may still exist, so do
            # NOT fall through to the review gate (double-handle).
            return True
        if not has_parked:
            return False
    try:
        await app_state.worker_execution_service.dispatch_resume(
            approval_id=approval_id,
            approved=approved,
            decided_by=decided_by,
            decision_reason=decision_reason,
        )
    except MemoryError, RecursionError:
        raise
    except AgentRuntimeNotConfiguredError:
        # A runtime-misconfiguration failure means the parked run can
        # NEVER resume (no engine/provider to resume into). Swallowing
        # it and returning True would mark the approval handled while
        # the work is silently stranded. Propagate so the controller
        # surfaces the real error instead of a false success.
        logger.error(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            note="resume dispatch failed -- runtime not configured",
        )
        raise
    except Exception as exc:
        # A transient dispatch failure (e.g. background-spawn hiccup)
        # must not 5xx the approve/reject response and must still
        # suppress the review-gate fall-through (the parked record is
        # intact -- resume_context has not run on this path -- so the
        # operator can re-trigger). Distinct from the hard
        # runtime-misconfiguration case re-raised above.
        logger.error(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="resume dispatch failed",
        )
    return True


async def _load_conversational_proposal(
    app_state: AppState,
    approval_id: str,
) -> tuple[bool, object | None]:
    """Resolve a conversational-intake proposal for *approval_id*.

    Returns a (owns_decision, proposal_or_none) tuple:

    * ``(False, None)``: the approval is NOT a conversational-intake
      one; the caller falls through to other resume flows.
    * ``(True, None)``: this flow owns the decision but there is no
      proposal row to act on (a logged no-op); the caller returns
      ``True`` without further work.
    * ``(True, proposal)``: this flow owns the decision and has a
      proposal to transition.

    Raises:
        ServiceUnavailableError: When the approval store read fails
            (``item is None``) or the proposal repo is not wired.
            Both are hard misconfigurations -- swallowing them would
            silently strand a decided conversational approval.
    """
    from synthorg.core.enums import ApprovalSource  # noqa: PLC0415
    from synthorg.persistence.conversational_proposal_protocol import (  # noqa: PLC0415
        ConversationalProposalFilterSpec,
    )

    item = await _reread_approval_item(app_state, approval_id)
    if item is None:
        logger.error(
            APPROVAL_GATE_CONVERSATIONAL_FAILED,
            approval_id=approval_id,
            note="approval reread failed; cannot determine source",
        )
        msg = "Approval state unavailable"
        raise ServiceUnavailableError(msg)
    if item.source is not ApprovalSource.CONVERSATIONAL_INTAKE:
        return False, None
    # This flow now owns the decision regardless of outcome.
    if not app_state.has_conversational_proposal_repo:
        logger.error(
            APPROVAL_GATE_CONVERSATIONAL_FAILED,
            approval_id=approval_id,
            note="conversational proposal repo not wired",
        )
        msg = "Conversational proposal repository unavailable"
        raise ServiceUnavailableError(msg)
    repo = app_state.conversational_proposal_repo
    proposals = await repo.query(
        ConversationalProposalFilterSpec(approval_id=approval_id),
    )
    if not proposals:
        logger.error(
            APPROVAL_GATE_CONVERSATIONAL_NO_PROPOSAL,
            approval_id=approval_id,
        )
        return True, None
    return True, proposals[0]


async def _reject_conversational_proposal(
    app_state: AppState,
    approval_id: str,
    proposal: object,
) -> None:
    """CAS the proposal from PENDING to REJECTED; pipeline never runs."""
    from synthorg.core.enums import ConversationalProposalStatus  # noqa: PLC0415

    repo = app_state.conversational_proposal_repo
    proposal_id = proposal.id  # type: ignore[attr-defined]
    transitioned = await repo.transition_if(
        proposal_id,
        ConversationalProposalStatus.PENDING,
        ConversationalProposalStatus.REJECTED,
    )
    if transitioned:
        logger.info(
            APPROVAL_GATE_CONVERSATIONAL_REJECTED,
            approval_id=approval_id,
            proposal_id=proposal_id,
        )
        return
    # Concurrent decision already transitioned this proposal (e.g.
    # duplicate approval-decision request). Surface the no-op so the
    # log doesn't claim a success we didn't make.
    logger.warning(
        APPROVAL_GATE_CONVERSATIONAL_FAILED,
        approval_id=approval_id,
        proposal_id=proposal_id,
        note="proposal already transitioned (reject path)",
    )


async def _execute_conversational_proposal(
    app_state: AppState,
    approval_id: str,
    proposal: object,
) -> None:
    """Acquire EXECUTING via CAS, run pipeline, finalize EXECUTED.

    On pipeline failure the EXECUTING state reverts to PENDING so
    the proposal stays retryable rather than locked in EXECUTING.

    Raises:
        ServiceUnavailableError: When the work pipeline is not wired
            at all (cannot run approved work without it).
    """
    from synthorg.core.enums import ConversationalProposalStatus  # noqa: PLC0415
    from synthorg.engine.pipeline.models import WorkItem  # noqa: PLC0415

    repo = app_state.conversational_proposal_repo
    proposal_id = proposal.id  # type: ignore[attr-defined]
    work_item_json = proposal.work_item_json  # type: ignore[attr-defined]

    if not app_state.has_work_pipeline:
        # Approved work can never run without a pipeline. Surface it
        # rather than marking the approval handled while the work is
        # silently stranded.
        logger.error(
            APPROVAL_GATE_CONVERSATIONAL_FAILED,
            approval_id=approval_id,
            proposal_id=proposal_id,
            note="work pipeline not configured",
        )
        msg = "Work pipeline unavailable"
        raise ServiceUnavailableError(msg)

    # PENDING -> EXECUTING CAS first, so concurrent decisions cannot
    # both drive the pipeline for the same proposal. Only the winner
    # of this transition runs the pipeline; the loser returns without
    # side-effects.
    acquired = await repo.transition_if(
        proposal_id,
        ConversationalProposalStatus.PENDING,
        ConversationalProposalStatus.EXECUTING,
    )
    if not acquired:
        logger.warning(
            APPROVAL_GATE_CONVERSATIONAL_FAILED,
            approval_id=approval_id,
            proposal_id=proposal_id,
            note="proposal already transitioned (execute-acquire path)",
        )
        return

    try:
        work_item = WorkItem.model_validate_json(work_item_json)
        await app_state.work_pipeline.run(work_item)
    except MemoryError, RecursionError:
        raise
    except ServiceUnavailableError:
        raise
    except Exception as exc:
        # The approve decision is already persisted; a pipeline failure
        # must not 5xx the response. Revert EXECUTING -> PENDING so the
        # proposal is retryable rather than stuck in EXECUTING forever.
        reverted = await repo.transition_if(
            proposal_id,
            ConversationalProposalStatus.EXECUTING,
            ConversationalProposalStatus.PENDING,
        )
        logger.error(
            APPROVAL_GATE_CONVERSATIONAL_FAILED,
            approval_id=approval_id,
            proposal_id=proposal_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note=(
                "pipeline run failed; proposal reverted to pending"
                if reverted
                else (
                    "pipeline run failed; proposal left in EXECUTING (revert lost race)"
                )
            ),
        )
        return

    transitioned = await repo.transition_if(
        proposal_id,
        ConversationalProposalStatus.EXECUTING,
        ConversationalProposalStatus.EXECUTED,
    )
    if transitioned:
        logger.info(
            APPROVAL_GATE_CONVERSATIONAL_EXECUTED,
            approval_id=approval_id,
            proposal_id=proposal_id,
        )
        return
    # The acquire CAS guarantees we are the only caller that ran the
    # pipeline, so this only happens if a non-approval-gate writer
    # mutated the proposal mid-flight (operator override, future
    # admin endpoint). Surface the no-op so the log doesn't claim a
    # CAS success we didn't make.
    logger.warning(
        APPROVAL_GATE_CONVERSATIONAL_FAILED,
        approval_id=approval_id,
        proposal_id=proposal_id,
        note=(
            "proposal mutated mid-execute (executing->executed CAS "
            "failed); pipeline run still succeeded"
        ),
    )


async def try_conversational_intake_resume(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
) -> bool:
    """Run a decided conversational-intake proposal, if this is one.

    Deterministic routing off the persisted
    :attr:`ApprovalItem.source` discriminator: only
    ``CONVERSATIONAL_INTAKE`` approvals are owned here; everything else
    returns ``False`` so the caller falls through to the parked-context
    / review-gate flows. Once owned, the decision is fully resolved on
    this path and ``True`` is returned even on failure so the approval
    is never double-handled.

    On approval the parked ``WorkItem`` is rebuilt from the proposal
    and driven through the work pipeline (still gated -- it only runs
    because a human approved); the proposal is then marked EXECUTED. On
    rejection the proposal is marked REJECTED and the pipeline is never
    touched.

    Raises:
        ServiceUnavailableError: When the approval store read fails,
            the proposal repo is not wired, or the work pipeline is
            missing on approve. All three are hard misconfigurations
            the operator must fix.
    """
    owns_decision, proposal = await _load_conversational_proposal(
        app_state, approval_id
    )
    if not owns_decision:
        return False
    if proposal is None:
        return True
    if not approved:
        await _reject_conversational_proposal(app_state, approval_id, proposal)
        return True
    await _execute_conversational_proposal(app_state, approval_id, proposal)
    return True


async def preflight_review_gate(
    review_gate: ReviewGateService,
    approval_id: str,
    task_id: str,
    *,
    decided_by: str | None = None,
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
    decided_by = resolve_decided_by(decided_by)
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
    decided_by: str | None = None,
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
    decided_by = resolve_decided_by(decided_by)
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
            error=safe_error_description(exc),
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
            error=safe_error_description(exc),
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
    decided_by: str | None = None,
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
    decided_by = resolve_decided_by(decided_by)
    logger.info(
        APPROVAL_GATE_RESUME_TRIGGERED,
        approval_id=approval_id,
        approved=approved,
        decided_by=decided_by,
        has_reason=decision_reason is not None,
    )

    # Flow 0: conversational-intake proposal. Inert (returns False)
    # for every non-conversational approval, so it cannot disturb the
    # parked-context / review-gate flows.
    if await try_conversational_intake_resume(
        app_state,
        approval_id,
        approved=approved,
    ):
        return

    # Flow 1: mid-execution parking.
    handled = await try_mid_execution_resume(
        app_state,
        approval_id,
        approved=approved,
        decided_by=decided_by,
        decision_reason=decision_reason,
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
