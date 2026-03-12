"""Task status sync — AgentEngine → TaskEngine integration.

Module-level functions extracted from ``AgentEngine`` to keep the
orchestrator file focused on execution flow.  Every function is
best-effort: sync failures are logged and swallowed so agent
execution is never blocked by a ``TaskEngine`` issue.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from ai_company.core.enums import TaskStatus
from ai_company.engine.errors import ExecutionStateError, TaskEngineError
from ai_company.engine.loop_protocol import TerminationReason
from ai_company.engine.task_engine_models import TransitionTaskMutation
from ai_company.observability import get_logger
from ai_company.observability.events.execution import (
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_SYNC_FAILED,
    EXECUTION_ENGINE_TASK_SYNCED,
    EXECUTION_ENGINE_TASK_TRANSITION,
)

if TYPE_CHECKING:
    from ai_company.engine.context import AgentContext
    from ai_company.engine.loop_protocol import ExecutionResult
    from ai_company.engine.task_engine import TaskEngine

logger = get_logger(__name__)


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
            instead of WARNING (severity only — sync remains best-effort
            regardless).

    Raises:
        MemoryError: Propagated unconditionally (non-recoverable).
        RecursionError: Propagated unconditionally (non-recoverable).
    """
    if task_engine is None:
        return

    log = logger.error if critical else logger.warning
    try:
        mutation = TransitionTaskMutation(
            request_id=uuid4().hex,
            requested_by=agent_id,
            task_id=task_id,
            target_status=target_status,
            reason=reason,
        )
        result = await task_engine.submit(mutation)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        error = (
            "TaskEngine unavailable"
            if isinstance(exc, TaskEngineError)
            else "Unexpected error syncing to TaskEngine"
        )
        log(
            EXECUTION_ENGINE_SYNC_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            target_status=target_status.value,
            error=error,
            exc_info=True,
        )
    else:
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
        log(
            EXECUTION_ENGINE_SYNC_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            target_status=target_status.value,
            error=result.error or "Mutation rejected (no error detail)",
            error_code=result.error_code,
        )


async def transition_task_if_needed(
    ctx: AgentContext,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
) -> AgentContext:
    """Transition ASSIGNED -> IN_PROGRESS; pass through IN_PROGRESS.

    Also syncs the transition to TaskEngine (best-effort).
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
) -> ExecutionResult:
    """Apply post-execution task transitions based on termination reason.

    COMPLETED triggers IN_PROGRESS -> IN_REVIEW -> COMPLETED.
    SHUTDOWN triggers current status -> INTERRUPTED.
    Each transition is synced to TaskEngine incrementally.
    Transition failures are logged but never discard the result.
    ``MemoryError`` and ``RecursionError`` propagate unconditionally.

    Returns the original ``execution_result`` unchanged if no
    transitions apply, or a copy with updated context reflecting
    the furthest-reached state on success or partial failure.
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

    try:
        ctx = await _transition_to_complete(ctx, agent_id, task_id, task_engine)
    except (ValueError, ExecutionStateError) as exc:
        logger.exception(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            error=f"Post-execution transition failed: {exc}",
        )
        # Return with whatever context _transition_to_complete reached
        # so the caller reflects the furthest-synced state.

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

    Returns the updated context.  The local transition (via
    ``with_task_transition``) is applied unconditionally; the remote
    sync is best-effort.
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


async def _transition_to_complete(
    ctx: AgentContext,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
) -> AgentContext:
    """Transition IN_PROGRESS -> IN_REVIEW -> COMPLETED with logging.

    Each step is synced to TaskEngine incrementally.
    """
    ctx = await _transition_and_sync(
        ctx,
        target_status=TaskStatus.IN_REVIEW,
        reason="Agent completed execution",
        agent_id=agent_id,
        task_id=task_id,
        task_engine=task_engine,
    )
    # TODO: Replace auto-complete with review gate (engine.md, step 10)
    return await _transition_and_sync(
        ctx,
        target_status=TaskStatus.COMPLETED,
        reason="Auto-completed (review gate not implemented)",
        agent_id=agent_id,
        task_id=task_id,
        task_engine=task_engine,
    )


async def _transition_to_interrupted(
    execution_result: ExecutionResult,
    ctx: AgentContext,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
) -> ExecutionResult:
    """Transition task to INTERRUPTED on graceful shutdown."""
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
        logger.exception(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            error=f"Post-execution INTERRUPTED transition failed: {exc}",
        )
        return execution_result
