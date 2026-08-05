"""The mechanical half of the post-execution task transitions.

``task_sync`` decides *which* status a finished run belongs in; this module
performs the write. Splitting them keeps the decision (the empty-run guard,
the unfinished-run table, the artifact probe) readable on its own, and gives
the two statuses that carry no decision at all (a shutdown interrupt, a
clarification park) a home away from it.

Every write here follows the same shape: apply the local transition, log it,
then sync best-effort to the central engine. A transition that raises leaves
the caller's result untouched rather than reporting a state change that did
not land.
"""

from synthorg.core.task_enums import TaskStatus
from synthorg.engine._task_sync_engine import sync_to_task_engine
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ExecutionStateError
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ERROR,
    EXECUTION_ENGINE_TASK_TRANSITION,
)

logger = get_logger(__name__)


async def transition_and_sync(
    ctx: AgentContext,
    *,
    target_status: TaskStatus,
    reason: str,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
    critical: bool = False,
) -> tuple[AgentContext, bool]:
    """Apply a local task transition, log it, and sync to TaskEngine.

    The local transition (via ``with_task_transition``) is applied
    unconditionally; the remote sync is best-effort.

    Returns:
        The updated :class:`AgentContext` after the local transition, and
        whether the central engine now reflects the transition (see
        :func:`sync_to_task_engine`).
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
    synced = await sync_to_task_engine(
        task_engine,
        target_status=target_status,
        task_id=task_id,
        agent_id=agent_id,
        reason=reason,
        critical=critical,
    )
    return ctx, synced


async def transition_to_interrupted(
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
        ctx, _ = await transition_and_sync(
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


async def transition_to_awaiting_input(
    execution_result: ExecutionResult,
    ctx: AgentContext,
    agent_id: str,
    task_id: str,
    task_engine: TaskEngine | None,
) -> ExecutionResult:
    """Transition task to AWAITING_INPUT on a clarification / decision park.

    Only the IN_PROGRESS entry status is moved; any other status is
    left untouched (the park may have happened before the ASSIGNED ->
    IN_PROGRESS transition landed, in which case there is nothing to
    pause). The resume path moves AWAITING_INPUT back to IN_PROGRESS
    before re-entering the loop.

    Returns:
        A copy of ``execution_result`` with the context updated to
        ``AWAITING_INPUT``; the original is returned unchanged when the
        task is not IN_PROGRESS or when the transition raises.
    """
    task_exec = ctx.task_execution
    if task_exec is None or task_exec.status != TaskStatus.IN_PROGRESS:
        return execution_result
    try:
        ctx, _ = await transition_and_sync(
            ctx,
            target_status=TaskStatus.AWAITING_INPUT,
            reason="Agent paused for human input",
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
            context="Post-execution AWAITING_INPUT transition failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return execution_result


__all__ = [
    "transition_and_sync",
    "transition_to_awaiting_input",
    "transition_to_interrupted",
]
