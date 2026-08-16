# module-kind: code
"""Review-approval creation for post-execution task sync.

Extracted from ``task_sync`` so the transition orchestrator stays within
its module-size budget.  Best-effort: a failure to create the approval
item is logged and swallowed so the execution result is never lost.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final
from uuid import UUID, uuid4

from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.task_review import (
    REVIEW_COMPLETION_ACTION_TYPE,
    REVIEW_FAILED_ACTION_TYPE,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.core.run_outcome import RunOutcome, risk_from_task_outcome
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_CREATED,
    APPROVAL_GATE_REVIEW_STORE_FAILED,
    APPROVAL_GATE_REVIEW_STORE_RETRYING,
)

if TYPE_CHECKING:
    # Cycle breaker: ``review_gate`` imports ``task_sync``, which imports this
    # module, so the gate's own result type is resolved for annotations only.
    from synthorg.engine.review_gate import ReviewRun

logger = get_logger(__name__)

_REVIEW_ACTION_TYPE: Final[str] = REVIEW_COMPLETION_ACTION_TYPE
_FAILED_ACTION_TYPE: Final[str] = REVIEW_FAILED_ACTION_TYPE

#: Who the settled approval records as the decider. The same identity the
#: gate is given, so the item and its decision record name one actor.
_AUTO_REVIEW_ACTOR: Final[str] = "system:auto-review"

# A FAILED-outcome approval is the only surface that carries a hard failure to
# the operator, so a transient store fault must not silently drop it first try.
_STORE_RETRY_MAX_ATTEMPTS: Final[int] = 3
_STORE_RETRY_BACKOFF_BASE_SECONDS: Final[float] = 0.05
_STORE_RETRY_BACKOFF_CAP_SECONDS: Final[float] = 0.5

# The guard below forces a conscious entry when a new RunOutcome member is
# added, so the best-effort creation path can never raise a KeyError on an
# unmapped outcome.
_OUTCOME_PHRASE: Final[MappingProxyType[RunOutcome, str]] = MappingProxyType(
    {
        RunOutcome.SUCCEEDED: "completed",
        RunOutcome.EMPTY: "completed with no produced artifacts",
        RunOutcome.FAILED: "failed",
    }
)

_missing_outcomes = set(RunOutcome) - set(_OUTCOME_PHRASE)
if _missing_outcomes:
    _phrase_msg = (
        f"_OUTCOME_PHRASE missing entries for: "
        f"{sorted(o.value for o in _missing_outcomes)}"
    )
    raise RuntimeError(_phrase_msg)
del _missing_outcomes


async def _persist_with_retry(
    add: Callable[[], Awaitable[None]],
    *,
    approval_id: UUID,
    task_id: str,
    agent_id: str,
    outcome: RunOutcome,
) -> bool:
    """Run the approval-store write with bounded transient-I/O retry.

    See ``docs/reference/retry-patterns.md`` (Pattern A): the store write is
    transient I/O, so a first-attempt fault must not drop the approval. The
    write is not idempotent under blind retry -- ``add`` raises ``ConflictError``
    when the id already exists -- so a ``ConflictError`` on a later attempt means
    a prior attempt's write landed even though its ack was lost, which is the
    outcome the caller wanted; it is treated as success, never a retryable fault.

    Returns:
        ``True`` when the write landed (including the already-persisted
        ``ConflictError`` case); ``False`` after the retry budget is exhausted.
        A dropped FAILED-outcome approval hides a hard failure from the operator
        (the whole point of the failure-aware queue), so it logs at ERROR; any
        non-FAILED outcome (including a run that produced no artifacts) is a
        WARNING, since the task is still visible IN_REVIEW on the board.
    """
    retry = GeneralRetryHandler(
        # A duplicate-id ConflictError is not transient: it means the item is
        # already persisted, so retrying would only re-raise the same conflict.
        retryable=lambda exc: not isinstance(exc, ConflictError),
        max_attempts=_STORE_RETRY_MAX_ATTEMPTS,
        base=_STORE_RETRY_BACKOFF_BASE_SECONDS,
        cap=_STORE_RETRY_BACKOFF_CAP_SECONDS,
        # A dedicated per-attempt event, not the generic engine-error one, so a
        # transient store retry here is not confused with an execution failure.
        event=APPROVAL_GATE_REVIEW_STORE_RETRYING,
        jitter=False,
    )
    try:
        await retry.execute(
            add, context="create_review_approval store write", task_id=task_id
        )
    except ConflictError:
        # The item with this id already exists: a prior attempt's write landed
        # (its ack lost to a transient blip). That is a persisted approval, not
        # a failure -- report success so the caller does not fire a false alert.
        return True
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- approval creation must not lose the run result
        reraise_critical(exc)
        emit = logger.error if outcome is RunOutcome.FAILED else logger.warning
        emit(
            APPROVAL_GATE_REVIEW_STORE_FAILED,
            approval_id=str(approval_id),
            task_id=task_id,
            agent_id=agent_id,
            outcome=outcome.value,
            context="Failed to create review approval after retries",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False
    return True


async def create_review_approval(
    approval_store: ApprovalStoreProtocol | None,
    *,
    agent_id: str,
    task_id: str,
    task: Task,
    outcome: RunOutcome,
) -> str | None:
    """Create an ApprovalItem for a task entering review (or failing).

    Best-effort: failures are logged and swallowed so the execution
    result is never lost.

    The risk level is derived from the task's stakes and the run outcome
    (a failure or empty run is never shown as ``LOW`` regardless of stakes),
    and a failed run carries a distinct action type so the review surface
    presents it as a failure rather than a routine completion. The title uses
    the task's name so the operator never sees a raw UUID.

    Args:
        approval_store: Store to create the item in, or ``None``.
        agent_id: Agent that produced the run under review.
        task_id: Task identifier (matches the approval's ``task_id``).
        task: The task under review (source of stakes + title).
        outcome: The run outcome driving risk level and action type.

    Returns:
        The approval_id on success, or ``None`` if no store or on error.
    """
    if approval_store is None:
        return None

    now = datetime.now(UTC)
    approval_id = uuid4()
    action_type = (
        _FAILED_ACTION_TYPE if outcome is RunOutcome.FAILED else _REVIEW_ACTION_TYPE
    )
    risk_level = risk_from_task_outcome(task.stakes, outcome)
    description = f"Agent {agent_id} {_OUTCOME_PHRASE[outcome]} task: {task.title}"
    # Local import breaks the ontology -> persistence -> budget ->
    # security -> engine -> core.approval cycle (see
    # security.service_escalation for the same pattern).
    from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

    item = ApprovalItem(
        id=approval_id,
        action_type=action_type,
        title=f"Review: {task.title}",
        description=description,
        requested_by=agent_id,
        risk_level=risk_level,
        created_at=now,
        task_id=task_id,
    )
    persisted = await _persist_with_retry(
        lambda: approval_store.add(item),
        approval_id=approval_id,
        task_id=task_id,
        agent_id=agent_id,
        outcome=outcome,
    )
    if not persisted:
        return None

    logger.info(
        APPROVAL_GATE_REVIEW_CREATED,
        approval_id=str(approval_id),
        task_id=task_id,
        agent_id=agent_id,
        outcome=outcome.value,
        risk_level=risk_level.value,
    )
    return str(approval_id)


async def settle_auto_reviewed_approval(
    approval_store: ApprovalStoreProtocol | None,
    *,
    approval_id: str | None,
    run: ReviewRun | None,
    task_id: str,
) -> None:
    """Settle the review approval an auto-review already decided.

    The gate decides the TASK and links this row as a foreign key, but
    nothing writes the row's own status, so a decided review stayed PENDING
    and the operator's queue kept asking them to decide it.

    Only a review that RAN and reached a verdict settles anything: a gate
    that is unwired, parked, or failed leaves the row pending on purpose,
    because a human still owes the decision. ``save_if_pending`` keeps that
    honest under a race, so a human who got there first is never overwritten.

    Args:
        approval_store: Store holding the item, or ``None``.
        approval_id: The item to settle, or ``None`` when none was created.
        run: The auto-review's result, or ``None`` when it did not run.
        task_id: The task under review, for the log line.
    """
    if approval_store is None or approval_id is None or run is None:
        return
    if run.outcome is None:
        return
    reworked = run.rework_reason
    approved = run.outcome.approved
    if not approved and reworked is None:
        # Decided against, but with nothing to say why. REJECTED requires a
        # reason, so leaving it pending is the honest outcome.
        return
    try:
        existing = await approval_store.get(NotBlankStr(approval_id))
        if existing is None or existing.status is not ApprovalStatus.PENDING:
            return
        settled = existing.model_copy(
            update={
                "status": (
                    ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
                ),
                "decided_at": datetime.now(UTC),
                "decided_by": NotBlankStr(_AUTO_REVIEW_ACTOR),
                "decision_reason": None if approved else NotBlankStr(reworked),
            }
        )
        await approval_store.save_if_pending(settled)
    except Exception as exc:  # noqa: BLE001 -- best-effort side channel
        # lint-allow: swallow-ok -- the task's own verdict already committed;
        # a stale queue row is worse reported than allowed to lose the run.
        # The read is inside the boundary for the same reason the write is:
        # an unreadable queue must not fail a run that already finished.
        reraise_critical(exc)
        logger.warning(
            APPROVAL_GATE_REVIEW_STORE_FAILED,
            approval_id=approval_id,
            task_id=task_id,
            context="Failed to settle the auto-reviewed approval",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
