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

Two waves racing for one subtask cannot both win. This writer reads before
it submits, so its read can be stale, but the decision is not made here: the
engine re-reads the row inside its serialised single-writer loop, and
``ASSIGNED -> ASSIGNED`` is not a legal hop, so the second wave's mutation is
refused and this writer fails its wave rather than quietly rewriting
``assigned_to`` under an agent that is already running.
"""

from typing import TYPE_CHECKING, Final
from uuid import uuid4

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.errors import CoordinationError
from synthorg.engine.parallel_models import AgentAssignment, ParallelExecutionGroup
from synthorg.engine.task_engine_models import TransitionTaskMutation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination import (
    COORDINATION_WAVE_ASSIGNMENT_RELEASE_FAILED,
    COORDINATION_WAVE_BUILT,
)

if TYPE_CHECKING:
    # Concrete service faked in tests; a runtime import would make typeguard
    # enforce a nominal isinstance the fakes cannot satisfy.
    from synthorg.engine.task_engine import TaskEngine

logger = get_logger(__name__)

#: Requester recorded on the assignment write, matching the coordinator
#: sentinel the parent-task walk uses.
_ASSIGNMENT_ACTOR: Final[str] = "coordinator"

#: Statuses in which a subtask is already owned by whoever ``assigned_to``
#: names. Half of the ownership test: the call site pairs this with
#: ``assigned_to == agent_id``, because a row in one of these statuses owned
#: by a *different* agent is a conflict, not a re-dispatch. Re-dispatch (a
#: replan wave, a resumed run) lands here for the same agent, and rewriting
#: the row would be a redundant hop the state machine has no reason to accept.
_ALREADY_OWNED: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS}
)

#: Recorded on a subtask released after its wave failed to assign a sibling.
#: The row says why it is not running rather than sitting ASSIGNED to an agent
#: that was never dispatched.
_RELEASE_REASON: Final[str] = (
    "Released: the wave this subtask belonged to could not assign every "
    "subtask, so it was never dispatched"
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
        assignments: list[AgentAssignment] = []
        moved: list[AgentAssignment] = []
        try:
            for candidate in group.assignments:
                persisted, was_moved = await self._persist_one(engine, candidate)
                assignments.append(persisted)
                if was_moved:
                    moved.append(persisted)
        except CoordinationError:
            await self._release(engine, tuple(moved))
            raise
        return group.model_copy(update={"assignments": tuple(assignments)})

    async def _release(
        self, engine: TaskEngine, moved: tuple[AgentAssignment, ...]
    ) -> None:
        """Move a failed wave's already-assigned subtasks out of ASSIGNED.

        A wave assigns one subtask at a time, so a refusal partway through
        leaves the ones before it owned by an agent that will never run: the
        dispatcher has already given up, and nothing else watches an ASSIGNED
        row with no runner. BLOCKED rather than CANCELLED because the work is
        still wanted and ``BLOCKED -> ASSIGNED`` is how a replan wave picks it
        back up, which CANCELLED would foreclose.

        Only rows this writer moved are released. A subtask another wave
        already owns was returned untouched, and rewriting it here would
        block work that is running.

        Args:
            engine: The engine that owns task status.
            moved: The assignments this call had already transitioned.
        """
        for assignment in moved:
            task_id = str(assignment.task.id)
            try:
                await engine.submit(
                    TransitionTaskMutation(
                        request_id=uuid4().hex,
                        requested_by=_ASSIGNMENT_ACTOR,
                        task_id=task_id,
                        target_status=TaskStatus.BLOCKED,
                        reason=_RELEASE_REASON,
                    )
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                # lint-allow: swallow-ok -- the wave failure is already being
                # raised; a failed release must not replace that diagnosis.
                reraise_critical(exc)
                logger.warning(
                    COORDINATION_WAVE_ASSIGNMENT_RELEASE_FAILED,
                    subtask_id=task_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

    async def _persist_one(
        self, engine: TaskEngine, assignment: AgentAssignment
    ) -> tuple[AgentAssignment, bool]:
        """Assign one subtask and return it carrying the engine's row.

        Returns:
            ``(assignment, moved)``: the assignment with ``task`` replaced by
            the engine's task, and whether this call transitioned the row. A
            subtask already owned by the same agent was not moved, so a later
            failure in the wave must not release it.

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
            return assignment.model_copy(update={"task": live}), False

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
            # ``live.status`` is what this dispatcher read, not necessarily
            # what the engine refused from: the engine re-reads the row under
            # its single-writer loop, so a wave racing this one has already
            # moved it. Both are named because the pair IS the diagnosis.
            msg = (
                f"Subtask {task_id!r} could not be assigned to agent "
                f"{agent_id!r} (read as {live.status.value!r} before "
                f"dispatch): "
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
        return assignment.model_copy(update={"task": result.task}), True


__all__ = ["AssignmentWriter"]
