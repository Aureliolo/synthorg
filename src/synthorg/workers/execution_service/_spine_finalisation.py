# module-kind: code
"""Best-effort finalisation of a failed background pipeline spine.

Persists the FAILED status and terminal error frame for a task whose
background spine crashed before it reached the loop, and guarantees the
cleanup runs to completion even when a shutdown drain cancels the awaiting
frame (so a crash never lingers in a non-terminal state).
"""

import asyncio
from typing import TYPE_CHECKING, Final

from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.engine._task_sync_engine import sync_to_task_engine

if TYPE_CHECKING:
    from synthorg.engine.agent_engine import AgentEngine
    from synthorg.engine.task_engine import TaskEngine

# Agent id stamped on a terminal RUN_ERROR projected for a background spine
# failure that never reached the loop (so no real agent identity resolved).
_SYSTEM_PIPELINE_AGENT_ID: Final[str] = "system:pipeline"


async def finalise_failed_spine(
    task_engine: TaskEngine, engine: AgentEngine, task: Task, reason: str
) -> None:
    """Persist the FAILED status and project the terminal error frame."""
    await sync_to_task_engine(
        task_engine,
        target_status=TaskStatus.FAILED,
        task_id=str(task.id),
        agent_id=_SYSTEM_PIPELINE_AGENT_ID,
        reason=reason,
        critical=True,
    )
    await engine.project_background_failure(
        task_id=str(task.id), agent_id=_SYSTEM_PIPELINE_AGENT_ID
    )


async def finalise_failed_spine_guarded(
    task_engine: TaskEngine, engine: AgentEngine, task: Task, reason: str
) -> None:
    """Finalise the FAILED spine, awaiting cleanup even under cancellation.

    ``asyncio.shield`` alone keeps the cleanup coroutine running when a
    shutdown-drain cancels this frame, but it does not wait for it: the
    awaiter unwinds while the transition + terminal frame may still be in
    flight. Retaining the task and awaiting it on cancellation (before
    re-raising) guarantees a crashed spine never lands in a non-terminal
    state.

    Raises:
        CancelledError: Re-raised after the cleanup completes when a
            shutdown drain cancels this frame.
    """
    cleanup = asyncio.ensure_future(
        finalise_failed_spine(task_engine, engine, task, reason)
    )
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        if not cleanup.done():
            await cleanup
        raise
