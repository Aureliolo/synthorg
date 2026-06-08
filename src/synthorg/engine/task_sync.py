"""Task status sync -- AgentEngine → TaskEngine integration.

Module-level functions extracted from ``AgentEngine`` to keep the
orchestrator file focused on execution flow.  Every function is
best-effort: sync failures are logged and swallowed so agent
execution is never blocked by a ``TaskEngine`` issue.
"""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ApprovalRiskLevel
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.errors import ExecutionStateError, TaskEngineError
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.task_engine_models import TransitionTaskMutation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_REVIEW_CREATED,
)
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_SYNC_FAILED,
    EXECUTION_ENGINE_TASK_SYNCED,
    EXECUTION_ENGINE_TASK_TRANSITION,
)

if TYPE_CHECKING:
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.engine.context import AgentContext
    from synthorg.engine.loop_protocol import ExecutionResult
    from synthorg.engine.task_engine import TaskEngine

logger = get_logger(__name__)

# Stepwise completion transitions: each (target_status, reason) pair
# is applied in order.  ``apply_post_execution_transitions`` updates
# ``ctx`` after each step so partial-failure always reflects the
# furthest-reached state.
_COMPLETION_STEPS: tuple[tuple[TaskStatus, str], ...] = (
    (TaskStatus.IN_REVIEW, "Agent completed execution -- awaiting review"),
)

_REVIEW_ACTION_TYPE: Final[str] = "review:task_completion"


async def sync_to_task_engine(  # noqa: PLR0913
    task_engine: TaskEngine | None,
    *,
    target_status: TaskStatus,
    task_id: str,
    agent_id: str,
    reason: str,
    critical: bool = False,
) -> None:
    """Sync a status transition to the centralized TaskEngine.

    Best-effort: failures are logged and swallowed so that agent
    execution is never blocked by a TaskEngine issue.
    ``MemoryError`` and ``RecursionError`` propagate unconditionally.

    Args:
        task_engine: The task engine to sync to, or ``None`` (no-op).
        target_status: The status to transition to.
        task_id: Task identifier.
        agent_id: Agent performing the transition.
        reason: Human-readable reason for the transition.
        critical: If ``True``, sync failure is logged at ERROR level
            instead of WARNING (severity only -- sync remains best-effort
            regardless).

    Raises:
        MemoryError: Propagated unconditionally (non-recoverable).
        RecursionError: Propagated unconditionally (non-recoverable).
        asyncio.CancelledError: Propagated so shutdown can proceed.
    """
    if task_engine is None:
        return

    try:
        mutation = TransitionTaskMutation(
            request_id=uuid4().hex,
            requested_by=agent_id,
            task_id=task_id,
            target_status=target_status,
            reason=reason,
        )
        result = await task_engine.submit(mutation)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        _log_sync_issue(
            critical=critical,
            agent_id=agent_id,
            task_id=task_id,
            target_status=target_status,
            error=(
                "TaskEngine unavailable"
                if isinstance(exc, TaskEngineError)
                else "Unexpected error syncing to TaskEngine"
            ),
        )
        return

    if result.success:
        logger.debug(
            EXECUTION_ENGINE_TASK_SYNCED,
            agent_id=agent_id,
            task_id=task_id,
            target_status=target_status.value,
            version=result.version,
        )
        return

    # Mutation was rejected (e.g. version conflict, invalid
    # transition, task not found).
    _log_sync_issue(
        critical=critical,
        agent_id=agent_id,
        task_id=task_id,
        target_status=target_status,
        error=result.error or "Mutation rejected (no error detail)",
        error_code=result.error_code,
    )


def _log_sync_issue(
    *,
    critical: bool,
    agent_id: str,
    task_id: str,
    target_status: TaskStatus,
    **extra: object,
) -> None:
    """Log a sync failure at ERROR (critical) or WARNING severity."""
    common = {
        "agent_id": agent_id,
        "task_id": task_id,
        "target_status": target_status.value,
        **extra,
    }
    if critical:
        logger.error(EXECUTION_ENGINE_SYNC_FAILED, **common)
    else:
        logger.warning(EXECUTION_ENGINE_SYNC_FAILED, **common)


async def transition_task_if_needed(
    ctx: AgentContext,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
) -> AgentContext:
    """Transition ASSIGNED -> IN_PROGRESS; pass through IN_PROGRESS.

    Also syncs the transition to TaskEngine (best-effort).

    Returns:
        The (possibly updated) :class:`AgentContext` with status
        transitioned to ``IN_PROGRESS`` when the entry status was
        ``ASSIGNED``; otherwise ``ctx`` unchanged.
    """
    if (
        ctx.task_execution is not None
        and ctx.task_execution.status == TaskStatus.ASSIGNED
    ):
        ctx = await _transition_and_sync(
            ctx,
            target_status=TaskStatus.IN_PROGRESS,
            reason="Engine starting execution",
            agent_id=agent_id,
            task_id=task_id,
            task_engine=task_engine,
            critical=True,
        )
    return ctx


async def apply_post_execution_transitions(
    execution_result: ExecutionResult,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
    approval_store: ApprovalStoreProtocol | None = None,
) -> ExecutionResult:
    """Apply post-execution task transitions based on termination reason.

    COMPLETED termination triggers the stepwise transitions defined
    in ``_COMPLETION_STEPS`` (currently: -> IN_REVIEW, awaiting review).
    SHUTDOWN triggers current status -> INTERRUPTED.
    Each transition is synced to TaskEngine incrementally.
    Transition failures are logged but never discard the result.
    ``MemoryError`` and ``RecursionError`` propagate unconditionally.

    When an ``approval_store`` is provided and the task reaches
    IN_REVIEW, an ``ApprovalItem`` is created so the human knows
    there is a task to review.

    Returns:
        The original ``execution_result`` unchanged if no transitions
        apply, or a copy with updated context reflecting the
        furthest-reached state on success or partial failure.
    """
    ctx = execution_result.context
    if ctx.task_execution is None:
        return execution_result

    reason = execution_result.termination_reason

    if reason == TerminationReason.SHUTDOWN:
        return await _transition_to_interrupted(
            execution_result, ctx, agent_id, task_id, task_engine
        )

    if reason != TerminationReason.COMPLETED:
        return execution_result

    # Apply IN_PROGRESS -> IN_REVIEW stepwise so that ``ctx`` always
    # reflects the furthest-reached state, even when one step raises
    # (partial-completion safety).
    for target, step_reason in _COMPLETION_STEPS:
        try:
            ctx = await _transition_and_sync(
                ctx,
                target_status=target,
                reason=step_reason,
                agent_id=agent_id,
                task_id=task_id,
                task_engine=task_engine,
            )
        except (ValueError, ExecutionStateError) as exc:
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                context="Post-execution transition failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            break

    # Create a review approval if the task reached IN_REVIEW.
    if (
        ctx.task_execution is not None
        and ctx.task_execution.status == TaskStatus.IN_REVIEW
    ):
        await _create_review_approval(
            approval_store, agent_id=agent_id, task_id=task_id
        )

    if ctx is execution_result.context:
        return execution_result
    return execution_result.model_copy(update={"context": ctx})


async def _transition_and_sync(  # noqa: PLR0913
    ctx: AgentContext,
    *,
    target_status: TaskStatus,
    reason: str,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
    critical: bool = False,
) -> AgentContext:
    """Apply a local task transition, log it, and sync to TaskEngine.

    The local transition (via ``with_task_transition``) is applied
    unconditionally; the remote sync is best-effort.

    Returns:
        The updated :class:`AgentContext` after the local transition.
    """
    prev_status = ctx.task_execution.status  # type: ignore[union-attr]
    ctx = ctx.with_task_transition(target_status, reason=reason)
    logger.info(
        EXECUTION_ENGINE_TASK_TRANSITION,
        agent_id=agent_id,
        task_id=task_id,
        from_status=prev_status.value,
        to_status=target_status.value,
    )
    await sync_to_task_engine(
        task_engine,
        target_status=target_status,
        task_id=task_id,
        agent_id=agent_id,
        reason=reason,
        critical=critical,
    )
    return ctx


async def _create_review_approval(
    approval_store: ApprovalStoreProtocol | None,
    *,
    agent_id: str,
    task_id: str,
) -> str | None:
    """Create an ApprovalItem for a task entering IN_REVIEW.

    Best-effort: failures are logged and swallowed so the
    execution result is never lost.

    Args:
        approval_store: Store to create the item in, or ``None``.
        agent_id: Agent that completed the task.
        task_id: Task identifier.

    Returns:
        The approval_id on success, or ``None`` if no store or on error.
    """
    if approval_store is None:
        return None

    now = datetime.now(UTC)
    approval_id = uuid4()
    # Local import breaks the ontology -> persistence -> budget ->
    # security -> engine -> core.approval cycle (see security.service
    # for the same pattern).
    from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

    item = ApprovalItem(
        id=approval_id,
        action_type=_REVIEW_ACTION_TYPE,
        title=f"Review task {task_id} completion",
        description=f"Agent {agent_id} completed task {task_id}",
        requested_by=agent_id,
        risk_level=ApprovalRiskLevel.LOW,
        created_at=now,
        task_id=task_id,
    )
    try:
        await approval_store.add(item)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            approval_id=approval_id,
            task_id=task_id,
            agent_id=agent_id,
            error="Failed to create review approval (non-fatal)",
        )
        return None

    logger.info(
        APPROVAL_GATE_REVIEW_CREATED,
        approval_id=str(approval_id),
        task_id=task_id,
        agent_id=agent_id,
    )
    return str(approval_id)


async def _transition_to_interrupted(
    execution_result: ExecutionResult,
    ctx: AgentContext,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
) -> ExecutionResult:
    """Transition task to INTERRUPTED on graceful shutdown.

    Returns:
        A copy of ``execution_result`` with the context updated to
        the ``INTERRUPTED`` status; the original ``execution_result``
        is returned unchanged when the transition raises.
    """
    try:
        ctx = await _transition_and_sync(
            ctx,
            target_status=TaskStatus.INTERRUPTED,
            reason="Graceful shutdown requested",
            agent_id=agent_id,
            task_id=task_id,
            task_engine=task_engine,
        )
        return execution_result.model_copy(update={"context": ctx})
    except (ValueError, ExecutionStateError) as exc:
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            context="Post-execution INTERRUPTED transition failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return execution_result
