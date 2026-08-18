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

import asyncio
import contextlib
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import BlockedReason, TaskStatus
from synthorg.engine.coordination._dependency_gate import (
    abandon_reason,
    block_reason,
    unmet_dependencies,
    unstarted_reason,
)
from synthorg.engine.errors import CoordinationError
from synthorg.engine.parallel_models import AgentAssignment, ParallelExecutionGroup
from synthorg.engine.task_engine_models import TransitionTaskMutation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination import (
    COORDINATION_WAVE_ASSIGNMENT_RELEASE_FAILED,
    COORDINATION_WAVE_BUILT,
    COORDINATION_WAVE_DEPENDENCY_UNMET,
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

#: Statuses a subtask can be abandoned from: it is still waiting to be
#: dispatched, so nothing it did owns its outcome. Anything else either ran
#: (and owns its own result) or is already parked with a reason that names
#: its actual dependency, which is more specific than "never reached".
_ABANDONABLE_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.CREATED, TaskStatus.ASSIGNED}
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

    async def gate_on_dependencies(
        self,
        group: ParallelExecutionGroup,
        dependencies: Mapping[str, tuple[str, ...]],
    ) -> ParallelExecutionGroup:
        """Drop the subtasks whose declared inputs did not arrive.

        Waves are dependency levels, so every subtask here was scheduled
        on the promise that the work it names finished. When that promise
        broke, running it anyway buys a turn budget's worth of spend for a
        task that can only fail against outputs nobody wrote.

        The dropped subtasks are parked BLOCKED rather than left where
        they were: an undispatched CREATED row has nothing watching it and
        no exit, so the plan can never derive a terminal status. BLOCKED
        with a named reason is what a replan wave picks back up.

        Args:
            group: The wave about to dispatch.
            dependencies: Each subtask id mapped to the ids it declares it
                depends on.

        Returns:
            The group carrying only the assignments whose inputs stand.
            When no engine is wired there is no status to read, so the
            group is returned unchanged.
        """
        engine = self._task_engine
        if engine is None:
            return group
        runnable: list[AgentAssignment] = []
        for assignment in group.assignments:
            task_id = str(assignment.task.id)
            declared = dependencies.get(task_id, ())
            statuses = {
                dependency_id: await self._dependency_status(engine, dependency_id)
                for dependency_id in declared
            }
            unmet = unmet_dependencies(statuses)
            if not unmet:
                runnable.append(assignment)
                continue
            logger.warning(
                COORDINATION_WAVE_DEPENDENCY_UNMET,
                subtask_id=task_id,
                group_id=group.group_id,
                unmet_dependencies=list(unmet),
            )
            if not await self._park_on_dependency(engine, assignment, unmet):
                # Dropping it now would leave a row at CREATED that nothing
                # watches and nothing can move, so its plan never derives a
                # terminal status and its project can never be deleted. Kept
                # in the wave instead: dispatching against dead inputs wastes
                # a turn budget and fails, which is a bad outcome, but it is
                # an outcome, and the row reaches a terminal the rollup can
                # conclude on.
                runnable.append(assignment)
        return group.model_copy(update={"assignments": tuple(runnable)})

    async def abandon_remaining(
        self,
        groups: Sequence[ParallelExecutionGroup],
        *,
        stopped_at: int,
    ) -> int:
        """Park every subtask of the waves this run will never reach.

        The other half of the gate. Gating parks what a wave scheduled on
        dead work, and covers only the wave being dispatched; a run that
        stops early abandons every wave AFTER it, whose subtasks would
        otherwise sit at CREATED with nothing watching them and no exit, so
        the plan could never derive a terminal status and its project could
        never be deleted.

        Every one of them parks, because a row left at CREATED has no exit and
        nothing watching it. What differs is what the park SAYS. A group is one
        round of agents, not one level of the DAG, so the groups after the one
        that stopped are a mix: some sit at a later level and genuinely may
        have lost an input, and some are siblings of the stopped group whose
        declared inputs are untouched. Only the first is a dependency failure.
        Telling them apart needs the level the group carries, since its
        position in the sequence cannot give that back.

        Args:
            groups: Every wave of the dispatch, in order.
            stopped_at: The index of the wave the run stopped at. That wave
                is not touched (its own outcome is already recorded); the
                waves after it are.

        Returns:
            How many subtasks were parked.
        """
        engine = self._task_engine
        if engine is None:
            return 0
        stopped_level = groups[stopped_at].dag_level if stopped_at < len(groups) else -1
        parked = 0
        for group in groups[stopped_at + 1 :]:
            depends_on_stopped = group.dag_level > stopped_level
            for assignment in group.assignments:
                parked += int(
                    await self._park_if_awaiting_dispatch(
                        engine,
                        assignment,
                        stopped_at=stopped_at,
                        depends_on_stopped=depends_on_stopped,
                    )
                )
        return parked

    async def abandon_stranded(
        self,
        group: ParallelExecutionGroup,
        *,
        stopped_at: int,
    ) -> int:
        """Park the rows of a wave that failed before dispatching them.

        ``abandon_remaining`` deliberately skips the wave the run stopped at,
        because a wave that RAN owns its own outcome. A wave that RAISED did
        not: ``persist`` gives up on the first refused hop, and the release
        path reverts only the rows it had already moved, so the rest of that
        wave never left CREATED. Nothing else parks them, and CREATED is not
        a non-delivering status, so the next wave's gate reads them as still
        on their way and dispatches against outputs nobody will write.

        Args:
            group: The wave that failed.
            stopped_at: Its index, for the reason string.

        Returns:
            How many subtasks were parked.
        """
        engine = self._task_engine
        if engine is None:
            return 0
        parked = 0
        for assignment in group.assignments:
            parked += int(
                await self._park_if_awaiting_dispatch(
                    engine,
                    assignment,
                    stopped_at=stopped_at,
                    depends_on_stopped=False,
                )
            )
        return parked

    async def _park_if_awaiting_dispatch(
        self,
        engine: TaskEngine,
        assignment: AgentAssignment,
        *,
        stopped_at: int,
        depends_on_stopped: bool,
    ) -> bool:
        """Park one row when it is still waiting to be dispatched.

        Best-effort per row, because this is bookkeeping running after the
        run has already stopped: the caller has a phase list describing what
        the run actually did, and letting one unreadable row raise past here
        discards it and reports a partially successful dispatch as a total
        failure. A row that cannot be read is left alone rather than parked,
        since its status is what decides whether parking it is even correct.

        Returns:
            Whether the row was parked.
        """
        try:
            live = await engine.get_task(str(assignment.task.id))
            # Only a row still waiting to be dispatched: anything that ran
            # owns its own outcome, and a row already parked by the gate
            # keeps the reason that names its actual dependency.
            if live is None or live.status not in _ABANDONABLE_STATUSES:
                return False
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- post-stop bookkeeping; the run's own
            # outcome is already recorded and must survive this
            reraise_critical(exc)
            logger.warning(
                COORDINATION_WAVE_DEPENDENCY_UNMET,
                task_id=str(assignment.task.id),
                note="could not read a subtask while abandoning; left unparked",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        # The write's own verdict, not the decision to attempt it: a refused
        # mutation leaves the row exactly where it was, and counting it as
        # parked reports a tail that was cleaned up when rows are still
        # sitting at CREATED with nothing watching them.
        return await self._park_abandoned(
            engine,
            assignment,
            stopped_at=stopped_at,
            depends_on_stopped=depends_on_stopped,
        )

    async def _park_abandoned(
        self,
        engine: TaskEngine,
        assignment: AgentAssignment,
        *,
        stopped_at: int,
        depends_on_stopped: bool,
    ) -> bool:
        """Park one subtask of a wave that was never reached.

        Args:
            engine: The engine that owns task status.
            assignment: The subtask that will not be dispatched.
            stopped_at: The wave the run stopped at, for the reason.
            depends_on_stopped: Whether this subtask sits at a level BELOW the
                one that stopped, and so may have lost a declared input. False
                for a sibling of the stopped wave, which is merely unstarted.

        Returns:
            Whether the park actually persisted.
        """
        task_id = str(assignment.task.id)
        reason = (
            abandon_reason(stopped_at)
            if depends_on_stopped
            else unstarted_reason(stopped_at)
        )
        blocked_reason = (
            BlockedReason.DEPENDENCY_FAILED
            if depends_on_stopped
            else BlockedReason.RUN_STOPPED
        )
        try:
            result = await engine.submit(
                TransitionTaskMutation(
                    request_id=uuid4().hex,
                    requested_by=_ASSIGNMENT_ACTOR,
                    task_id=task_id,
                    target_status=TaskStatus.BLOCKED,
                    reason=reason,
                    overrides={"blocked_reason": blocked_reason},
                )
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- one contended row must not stop the
            # rest of the tail from being parked, and this line is what says
            # the row was left where it was.
            reraise_critical(exc)
            logger.warning(
                COORDINATION_WAVE_DEPENDENCY_UNMET,
                subtask_id=task_id,
                parked=False,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        if not result.success:
            logger.warning(
                COORDINATION_WAVE_DEPENDENCY_UNMET,
                subtask_id=task_id,
                parked=False,
                error_type="TaskMutationRejected",
                error=result.error or "park rejected with no error detail",
            )
            return False
        return True

    @staticmethod
    async def _dependency_status(
        engine: TaskEngine, dependency_id: str
    ) -> TaskStatus | None:
        """Read one dependency's status from the engine.

        Returns:
            The status the engine holds, or ``None`` when it holds no row.
        """
        live = await engine.get_task(dependency_id)
        return None if live is None else live.status

    async def _park_on_dependency(
        self,
        engine: TaskEngine,
        assignment: AgentAssignment,
        unmet: tuple[str, ...],
    ) -> bool:
        """Park one subtask whose inputs did not arrive.

        Args:
            engine: The engine that owns task status.
            assignment: The subtask that will not be dispatched.
            unmet: The dependency ids that did not deliver.

        Returns:
            Whether the park actually persisted. The caller drops the subtask
            from the wave on the strength of this, so reporting a refused
            write as done is what strands the row.
        """
        task_id = str(assignment.task.id)
        try:
            result = await engine.submit(
                TransitionTaskMutation(
                    request_id=uuid4().hex,
                    requested_by=_ASSIGNMENT_ACTOR,
                    task_id=task_id,
                    target_status=TaskStatus.BLOCKED,
                    reason=block_reason(unmet),
                    overrides={"blocked_reason": BlockedReason.DEPENDENCY_FAILED},
                )
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- the wave still runs its healthy
            # subtasks; a failed park must not take them down with it, and
            # this line is what says the row was left where it was.
            reraise_critical(exc)
            logger.warning(
                COORDINATION_WAVE_DEPENDENCY_UNMET,
                subtask_id=task_id,
                parked=False,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        if not result.success:
            # The engine refuses by returning, not by raising, so an
            # unchecked result reads as a park that happened.
            logger.warning(
                COORDINATION_WAVE_DEPENDENCY_UNMET,
                subtask_id=task_id,
                parked=False,
                error_type="TaskMutationRejected",
                error=result.error or "park rejected with no error detail",
            )
            return False
        return True

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
            CancelledError: Re-raised after the rows already assigned are
                released, so a cancelled wave leaves nothing owned by an
                agent that will never run.
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
        # Any failure, not only a refused assignment: ``get_task`` and
        # ``submit`` also raise engine and persistence errors, and a wave that
        # dies on one of those leaks the same half-assigned rows. Cancellation
        # is named alongside them because it is not an ``Exception`` and leaks
        # exactly the same way. Re-raised unchanged so the compensation never
        # replaces the diagnosis.
        except Exception, asyncio.CancelledError:
            # Shielded: the release is the compensation for a cancellation, so
            # letting that same cancellation abort it would leave the rows the
            # handler exists to free still owned by nobody.
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(self._release(engine, tuple(moved)))
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
                result = await engine.submit(
                    TransitionTaskMutation(
                        request_id=uuid4().hex,
                        requested_by=_ASSIGNMENT_ACTOR,
                        task_id=task_id,
                        target_status=TaskStatus.BLOCKED,
                        reason=_RELEASE_REASON,
                        # Named so a rule written for the review gate's
                        # escalation cannot mistake a released subtask for a
                        # task a human was asked about.
                        overrides={"blocked_reason": BlockedReason.WAVE_RELEASED},
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
                continue
            if not result.success:
                # The engine refuses by returning, not by raising, so an
                # unchecked result reads as a release that happened. The row
                # is still ASSIGNED to an agent nothing will run, and this
                # line is the only place that says so.
                logger.warning(
                    COORDINATION_WAVE_ASSIGNMENT_RELEASE_FAILED,
                    subtask_id=task_id,
                    error_type="TaskMutationRejected",
                    error=result.error or "release rejected with no error detail",
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
