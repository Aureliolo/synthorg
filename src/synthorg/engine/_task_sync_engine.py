"""Best-effort status sync from the AgentEngine to the central TaskEngine.

Extracted from ``task_sync`` so the post-execution transition orchestrator
stays within its module-size budget. The sync is best-effort (failures are
logged and swallowed so agent execution is never blocked by a ``TaskEngine``
issue), but returns whether the central engine actually applied the transition
so a caller can gate downstream state on it.
"""

import asyncio
from uuid import uuid4

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.errors import TaskEngineError
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TransitionTaskMutation
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_SYNC_FAILED,
    EXECUTION_ENGINE_TASK_SYNCED,
)

logger = get_logger(__name__)


async def sync_to_task_engine(  # noqa: PLR0913
    task_engine: TaskEngine | None,
    *,
    target_status: TaskStatus,
    task_id: str,
    agent_id: str,
    reason: str,
    critical: bool = False,
) -> bool:
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

    Returns:
        ``True`` when the central engine now reflects the transition
        (no engine wired, so the local state is authoritative; or the
        mutation succeeded); ``False`` when an engine is wired but the
        mutation was swallowed or rejected, so the central task is still
        in its prior status. A caller that persists downstream state
        keyed on the new status (e.g. a failure approval) must gate on
        this so it never references a status the engine has not applied.

    Raises:
        MemoryError: Propagated unconditionally (non-recoverable).
        RecursionError: Propagated unconditionally (non-recoverable).
        asyncio.CancelledError: Propagated so shutdown can proceed.
    """
    if task_engine is None:
        return True

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
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort side channel
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
        return False

    if result.success:
        logger.debug(
            EXECUTION_ENGINE_TASK_SYNCED,
            agent_id=agent_id,
            task_id=task_id,
            target_status=target_status.value,
            version=result.version,
        )
        return True

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
    return False


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
