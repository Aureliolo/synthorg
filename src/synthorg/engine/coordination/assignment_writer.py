# module-kind: service
"""Persist a wave's assignments before the wave dispatches.

Routing selects an agent per subtask, but the selection is only real once
the central engine holds it: the engine validates every hop, and every
later sync is validated from the status it holds. A dispatcher that
carried its own ASSIGNED copy ahead of that write ran agents whose entry
transition the engine then refused, so the work happened against rows
that never left ``created``.

The engine's row is therefore the one owner of a subtask's status, and
this writer is the only place the coordination path asks it to move.
"""

from typing import TYPE_CHECKING, Final
from uuid import uuid4

from synthorg.core.task_enums import TaskStatus
from synthorg.engine.errors import CoordinationError
from synthorg.engine.parallel_models import AgentAssignment, ParallelExecutionGroup
from synthorg.engine.task_engine_models import TransitionTaskMutation
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import COORDINATION_WAVE_BUILT

if TYPE_CHECKING:
    # Concrete service faked in tests; a runtime import would make typeguard
    # enforce a nominal isinstance the fakes cannot satisfy.
    from synthorg.engine.task_engine import TaskEngine

logger = get_logger(__name__)

#: Requester recorded on the assignment write, matching the coordinator
#: sentinel the parent-task walk uses.
_ASSIGNMENT_ACTOR: Final[str] = "coordinator"

#: Statuses that already mean "this agent owns this subtask and may run it".
#: Re-dispatch (a replan wave, a resumed run) lands here, and rewriting the
#: row would be a redundant hop the state machine has no reason to accept.
_ALREADY_OWNED: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS}
)


class AssignmentWriter:
    """Move each of a wave's subtasks to ASSIGNED in the central engine.

    Args:
        task_engine: The engine that owns task status, or ``None`` when
            the coordination service was built without one (the local
            copy is then authoritative because nothing else holds a row).
    """

    __slots__ = ("_task_engine",)

    def __init__(self, task_engine: TaskEngine | None) -> None:
        self._task_engine = task_engine

    async def persist(self, group: ParallelExecutionGroup) -> ParallelExecutionGroup:
        """Assign every subtask in *group*, returning it rebuilt from the engine.

        Args:
            group: The wave about to dispatch.

        Returns:
            The group whose assignments carry the tasks exactly as the
            engine now holds them, or *group* unchanged when no engine is
            wired.

        Raises:
            CoordinationError: When a subtask could not be assigned. The
                wave fails rather than dispatching an agent against a row
                the engine did not move.
        """
        engine = self._task_engine
        if engine is None:
            return group
        assignments = tuple(
            [await self._persist_one(engine, a) for a in group.assignments]
        )
        return group.model_copy(update={"assignments": assignments})

    async def _persist_one(
        self, engine: TaskEngine, assignment: AgentAssignment
    ) -> AgentAssignment:
        """Assign one subtask and return it carrying the engine's row.

        Returns:
            The assignment with ``task`` replaced by the engine's task.

        Raises:
            CoordinationError: When the subtask has no row, or the
                assignment was refused.
        """
        task_id = str(assignment.task.id)
        agent_id = str(assignment.identity.id)
        live = await engine.get_task(task_id)
        if live is None:
            msg = (
                f"Subtask {task_id!r} routed to agent {agent_id!r} no longer "
                "exists in the task engine"
            )
            raise CoordinationError(msg)

        if live.status in _ALREADY_OWNED and live.assigned_to == agent_id:
            return assignment.model_copy(update={"task": live})

        result = await engine.submit(
            TransitionTaskMutation(
                request_id=uuid4().hex,
                requested_by=_ASSIGNMENT_ACTOR,
                task_id=task_id,
                target_status=TaskStatus.ASSIGNED,
                reason=f"Routed to agent {agent_id} for wave dispatch",
                overrides={"assigned_to": agent_id},
            )
        )
        if not result.success or result.task is None:
            msg = (
                f"Subtask {task_id!r} could not be assigned to agent "
                f"{agent_id!r} (task is {live.status.value!r}): "
                f"{result.error or 'mutation rejected with no error detail'}"
            )
            raise CoordinationError(msg)

        logger.debug(
            COORDINATION_WAVE_BUILT,
            subtask_id=task_id,
            agent_id=agent_id,
            from_status=live.status.value,
            to_status=TaskStatus.ASSIGNED.value,
        )
        return assignment.model_copy(update={"task": result.task})


__all__ = ["AssignmentWriter"]
