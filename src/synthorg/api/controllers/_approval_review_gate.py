"""Review-gate helpers for the approvals controller.

Isolates the review-gate flow (mid-execution resume vs review-gate
transition) from the controller CRUD logic so the resume and
gate-transition paths can be tested in isolation.

Exposes:
- :func:`try_mid_execution_resume` -- resume parked context path.
- :func:`preflight_review_gate` -- pre-save self-review / task check.
- :func:`try_review_gate_transition` -- post-save IN_REVIEW transition.
- :func:`signal_resume_intent` -- orchestrates both flows.
"""

from synthorg.api.controllers._approval_initiative_extension import (
    try_initiative_extension_resume,
)
from synthorg.api.controllers._approval_initiative_stall import (
    try_initiative_stall_resume,
)
from synthorg.api.controllers._approval_org_hire import try_org_hire_resume
from synthorg.api.controllers._conversational_resume import (
    _reread_approval_item,
    try_conversational_intake_resume,
    try_conversational_invite_resume,
)
from synthorg.api.controllers._plan_review_resume import try_plan_review_resume
from synthorg.api.controllers._project_decision_record import record_project_decision
from synthorg.api.state import AppState
from synthorg.approval.resume_annotations import resume_annotations
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.actor_context import resolve_decided_by
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from synthorg.engine.errors import (
    SelfReviewError,
    TaskInternalError,
    TaskMutationError,
    TaskNotFoundError,
    TaskVersionConflictError,
)
from synthorg.engine.review_gate import ReviewGateService
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_FAILED,
    APPROVAL_GATE_RESUME_TRIGGERED,
    APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
    APPROVAL_GATE_TASK_NOT_FOUND,
)
from synthorg.observability.events.security import (
    SECURITY_APPROVAL_SELF_REVIEW_PREVENTED,
)
from synthorg.workers.state import worker_execution_service_of

logger = get_logger(__name__)


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
    The ``has_parked_context`` probe is used only as a logged fallback
    for the degenerate case where the just-decided approval cannot be
    re-read (it should always be present here, since the caller
    persisted the decision immediately before).

    Returns ``True`` when the mid-execution flow is responsible for
    this approval so the caller does not also run the review-gate
    transition. Returns ``False`` when the approval is review-gate
    bound (e.g. a hiring/promotion approval) so the caller falls
    through to the review gate.

    Returns:
        ``True`` or ``False`` reflecting the condition.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
        AgentRuntimeNotConfiguredError: Raised on the corresponding failure path.
    """
    from synthorg.approval.enums import ApprovalSource  # noqa: PLC0415

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
        gate = app_state.slice(ApprovalStateSlice).gate
        if gate is None:
            return False
        try:
            has_parked = await gate.has_parked_context(approval_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
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
        await worker_execution_service_of(app_state).dispatch_resume(
            approval_id=approval_id,
            approved=approved,
            decided_by=decided_by,
            decision_reason=decision_reason,
            annotations=resume_annotations(item, approved=approved),
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
            error_type="AgentRuntimeNotConfiguredError",
            error="agent runtime not configured",
            note="resume dispatch failed -- runtime not configured",
        )
        raise
    except Exception as exc:  # noqa: BLE001 -- transient dispatch: don't 5xx
        # A transient dispatch failure (e.g. background-spawn hiccup)
        # must not 5xx the approve/reject response and must still
        # suppress the review-gate fall-through (the parked record is
        # intact -- resume_context has not run on this path -- so the
        # operator can re-trigger). Distinct from the hard
        # runtime-misconfiguration case re-raised above.
        log_exception_redacted(
            logger,
            APPROVAL_GATE_RESUME_FAILED,
            exc,
            approval_id=approval_id,
            note="resume dispatch failed",
        )
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
        TaskInternalError: A task-engine internal fault propagates as its
            faithful 500 ``ENGINE_ERROR`` via the centralised handler.
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
        # A task-engine internal fault is a 500 ENGINE_ERROR, not a
        # not-wired-yet 503: let it propagate via handle_domain_error
        # rather than mislabelling it SERVICE_UNAVAILABLE. The centralised
        # handler also logs the 5xx; this approval-scoped warning carries
        # the fault type so the audit event stands alone without a join.
        logger.warning(
            APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
            approval_id=approval_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise


async def try_review_gate_transition(
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
    and task existence, and that ``decided_by`` has already been resolved
    by the caller (``signal_resume_intent`` resolves it once for its own
    logging and the sibling resume flows, so re-resolving here would be
    redundant).  Surfaces engine-layer failures (task mutation, version
    conflict, persistence) as API errors so the caller sees a meaningful
    status code instead of a silent 200 OK with no state change.

    Delegates to :meth:`ReviewGateService.dispatch_completion`, which
    backgrounds a gated approval (a configured adversarial gate runs an
    inline AgentEngine) so the HTTP response is not blocked by the gate
    evaluation; the task holds in IN_REVIEW until the background gate
    transitions it. The inline (non-gated) path still surfaces the
    engine-layer failures below.

    Raises:
        TaskNotFoundError: When the task disappears between the preflight
            and the transition; re-raised with a redacted message so the
            client keeps the ``TASK_NOT_FOUND`` code without the UUID.
        TaskVersionConflictError: When the task version conflicts; re-raised
            (redacted) so the client keeps the retryable
            ``TASK_VERSION_CONFLICT`` code and knows to re-read and retry.
        ConflictError: When the task left ``IN_REVIEW`` before the decision
            landed (a base ``TaskMutationError`` invalid edge), surfaced as
            a redacted 409 rather than a raw 422 with internal detail.
        ForbiddenError: When a late self-review race is detected
            (agent reassigned between preflight and transition).
        TaskInternalError: A task-engine internal fault propagates as its
            faithful 500 ``ENGINE_ERROR`` via the centralised handler.
    """
    try:
        await review_gate.dispatch_completion(
            task_id=task_id,
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
        # Re-raise the discriminating type (preserving its TASK_NOT_FOUND code)
        # with a redacted message, so the client keeps the machine-branchable
        # signal without seeing internal task UUIDs.
        not_found_msg = "Associated task could not be found"
        raise TaskNotFoundError(not_found_msg) from exc
    except TaskVersionConflictError as exc:
        logger.warning(
            APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
            approval_id=approval_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        # Preserve the retryable TASK_VERSION_CONFLICT code (so the client
        # knows to re-read and retry) while redacting internal detail.
        conflict_msg = "A concurrent modification was detected; retry the request"
        raise TaskVersionConflictError(conflict_msg) from exc
    except TaskMutationError as exc:
        # Base-family mutation error (e.g. the task left IN_REVIEW before the
        # decision landed, so the edge is no longer legal): a genuine state
        # conflict, not a silent success. Surface a redacted 409 rather than
        # letting the raw 422 propagate with internal detail.
        logger.warning(
            APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
            approval_id=approval_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        mutation_msg = "The task is no longer in a reviewable state"
        raise ConflictError(mutation_msg) from exc
    except TaskInternalError as exc:
        # A task-engine internal fault is a 500 ENGINE_ERROR, not a
        # not-wired-yet 503: let it propagate via handle_domain_error
        # rather than mislabelling it SERVICE_UNAVAILABLE. The centralised
        # handler also logs the 5xx; this approval-scoped warning carries
        # the fault type so the audit event stands alone without a join.
        logger.warning(
            APPROVAL_GATE_REVIEW_TRANSITION_FAILED,
            approval_id=approval_id,
            task_id=task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise


async def signal_resume_intent(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
    decided_by: str | None = None,
    decision_reason: str | None = None,
    task_id: str | None = None,
) -> None:
    """Execute the resume or review-gate flow for a decided approval.

    Routed deterministically off the persisted ``ApprovalItem.source``:

    0. **Conversational intake** (:func:`try_conversational_intake_resume`):
       run / reject a parked work proposal.
    0.5. **Agent invite** (:func:`try_conversational_invite_resume`):
       add / decline an agent-initiated invite on consent.
    0.7. **Plan approval** (:func:`try_plan_review_resume`):
       dispatch the parked plan on approval, or cancel the parent task.
    0.75. **Stalled initiative** (:func:`try_initiative_stall_resume`):
       replan it on the operator's authority, or fail it with the stall reason.
    0.76. **Extension ask** (:func:`try_initiative_extension_resume`):
       extend the workstream, or leave it as delivered.
    0.8. **Project decision** (:func:`record_project_decision`):
       record a project-shaping decision, then fall through.
    0.9. **Org hire** (:func:`try_org_hire_resume`):
       instantiate the approved hire, or mark the request rejected.
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

    # Flow 0.5: agent-initiated invite consent. Inert for every
    # non-invite approval. Repo-direct + ungated, so consent resolves
    # even after the invite feature is toggled off.
    if await try_conversational_invite_resume(
        app_state,
        approval_id,
        approved=approved,
        decided_by=decided_by,
    ):
        return

    # Flow 0.7: what a plan review parked. Inert for everything else. The plan's
    # own approval dispatches the parked plan, or cancels the parent task on
    # rejection; a question parked off that plan settles onto the durable plan
    # and builds nothing. A question must not reach the flows below, which read
    # it as a task-completion review and refuse it.
    if await try_plan_review_resume(
        app_state,
        approval_id,
        approved=approved,
        decided_by=decided_by,
        decision_reason=decision_reason,
    ):
        return

    # Flow 0.75: a stalled initiative the operator was asked about. Inert for
    # everything else. It has to claim the item HERE rather than lower down:
    # the decision carries the objective task's id, and an unclaimed item with
    # a task_id reaches the review gate below, which reads it as a completion
    # review and refuses it.
    if await try_initiative_stall_resume(
        app_state,
        approval_id,
        approved=approved,
        decided_by=decided_by,
    ):
        return

    # Flow 0.76: an extension ask parked on the autonomy gate. Same claim
    # reason as flow 0.75.
    if await try_initiative_extension_resume(
        app_state,
        approval_id,
        approved=approved,
        decided_by=decided_by,
    ):
        return

    # Flow 0.8: project decision. Records an approved decision:project answer
    # as a brain DECISION entry, then falls through so the parked agent still
    # resumes with the choice injected (mid-execution flow below). Never
    # short-circuits and never raises for a routine miss.
    await record_project_decision(
        app_state,
        approval_id,
        approved=approved,
        decided_by=decided_by,
        decision_reason=decision_reason,
    )

    # Flow 0.9: org hire. Inert for every non-hiring approval. A hiring item
    # carries no task_id, so without this flow it fell through the whole
    # chain and the approved hire registered nobody.
    if await try_org_hire_resume(
        app_state,
        approval_id,
        approved=approved,
        decided_by=decided_by,
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
    review_gate = app_state.slice(ApprovalStateSlice).review_gate
    if review_gate is not None and task_id is not None:
        await try_review_gate_transition(
            review_gate,
            approval_id,
            task_id,
            approved=approved,
            decided_by=decided_by,
            decision_reason=decision_reason,
        )
