"""Live org-activity aggregation for the mission-control cockpit.

Builds a :class:`LiveActivitySnapshot` of in-flight work: who is working
on what, accumulated cost per agent, and stuck / runaway flags derived
from operator-tuned thresholds.

Two stores answer per-task activity and the run's state decides which:
the live ``AgentRuntimeState`` row while the agent still holds the task
(written per turn by the engine), and the flight-recorder frames once the
run has finished (built from its result). One question, one answer per
task, chosen by whether the work is still moving.
"""

import asyncio
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Final, NoReturn

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical_unwrapped
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_state import AgentRuntimeState, ExecutionStatus
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cockpit import (
    COCKPIT_RUNAWAY_DETECTED,
    COCKPIT_SNAPSHOT_FAILED,
    COCKPIT_SNAPSHOT_PUBLISHED,
    COCKPIT_STUCK_DETECTED,
)
from synthorg.persistence.agent_state_protocol import AgentStateRepository
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameFilterSpec,
    FlightRecorderFrameRepository,
)

logger = get_logger(__name__)

_PERCENT_DIVISOR: Final[float] = 100.0
# Fallback client poll cadence when the cockpit controller does not stamp
# the operator-tuned ``cockpit.snapshot_interval_seconds`` onto a snapshot.
_DEFAULT_SNAPSHOT_INTERVAL_SECONDS: Final[float] = 5.0
_ACTIVE_STATUSES: Final[tuple[TaskStatus, ...]] = (
    TaskStatus.IN_PROGRESS,
    TaskStatus.BLOCKED,
)


def _sum_costs(costs: Iterable[float]) -> float:
    """Sum costs known to share one budget currency by construction.

    Returns:
        Arithmetic sum of ``costs``.
    """
    return sum(costs)  # lint-allow: currency-aggregation -- single budget


def _fail_snapshot(
    group: BaseExceptionGroup[Exception],
    *,
    status: TaskStatus,
) -> NoReturn:
    """Re-raise a failed activity fan-out as the error the store reported.

    The repositories behind a row already raise typed ``DomainError``
    subclasses, but a ``TaskGroup`` wraps whatever a child raises, so what
    would otherwise leave this boundary is a group rather than the typed
    error. The API's exception handler matches on the domain hierarchy, so an
    escaping group is served as an unclassified 500 while the store itself
    reported something it could have named.

    A row is not defaulted on the way past: a task whose spend cannot be read
    is not a task with no spend, and publishing it as one under-reports work
    against the budget the runaway check compares to.

    Raises:
        BaseException: The first leaf of *group*, chained from it. Groups
            nest when a child is itself a group, so the leftmost spine is
            walked rather than taking ``exceptions[0]`` and raising another
            wrapper.
    """
    cause: BaseException = group
    while isinstance(cause, BaseExceptionGroup) and cause.exceptions:
        cause = cause.exceptions[0]
    logger.warning(
        COCKPIT_SNAPSHOT_FAILED,
        status=status.value,
        error_type=type(cause).__name__,
        error=safe_error_description(cause),
    )
    raise cause from group


class AgentActivity(BaseModel):
    """One agent's in-flight activity in the live snapshot."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent working the task")
    agent_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of the working agent, resolved at the read"
        " boundary; ``None`` when the roster does not cover them",
    )
    task_id: NotBlankStr = Field(description="Task being worked")
    task_title: NotBlankStr | None = Field(
        default=None,
        description="Title of the task being worked",
    )
    execution_id: NotBlankStr | None = Field(
        default=None,
        description="Execution id of the latest recorded turn, when any",
    )
    status: TaskStatus = Field(description="Current task status")
    turn_count: int = Field(ge=0, description="Turns recorded so far")
    cost: float = Field(ge=0.0, description="Accumulated cost for the task")
    last_active: AwareDatetime | None = Field(
        default=None,
        description="Timestamp of the latest recorded turn, when any",
    )
    is_stuck: bool = Field(description="Idle beyond the stuck threshold")
    is_runaway: bool = Field(description="Cost beyond the runaway threshold")


class LiveActivitySnapshot(BaseModel):
    """Aggregate snapshot of in-flight org activity."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    timestamp: AwareDatetime = Field(description="When the snapshot was built")
    agents: tuple[AgentActivity, ...] = Field(
        default=(),
        description="Per-task activity for active work",
    )
    total_cost: float = Field(default=0.0, ge=0.0, description="Summed active cost")
    active_count: int = Field(default=0, ge=0, description="Active task count")
    stuck_agents: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Agent ids flagged stuck",
    )
    runaway_agents: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Agent ids flagged runaway",
    )
    poll_interval_seconds: float = Field(
        default=_DEFAULT_SNAPSHOT_INTERVAL_SECONDS,
        gt=0.0,
        description=(
            "Operator-tuned client poll cadence in seconds"
            " (cockpit.snapshot_interval_seconds)."
        ),
    )


class CockpitService:
    """Aggregates in-flight work into a live activity snapshot."""

    def __init__(
        self,
        task_engine: TaskEngine,
        flight_recorder_frames: FlightRecorderFrameRepository,
        *,
        agent_states: AgentStateRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._task_engine = task_engine
        self._frames = flight_recorder_frames
        self._agent_states = agent_states
        self._clock = clock or SystemClock()

    async def get_live_snapshot(
        self,
        *,
        stuck_idle_minutes: float,
        runaway_cost_percent: float,
    ) -> LiveActivitySnapshot:
        """Build a snapshot of active work with stuck / runaway flags.

        Thresholds are passed in (resolved by the controller at request
        time) so the service stays pure and free of a settings-resolver
        dependency at wire time. Per-task activity rows fan out across
        an ``asyncio.TaskGroup`` so snapshot latency stays bounded by
        the slowest single ``get_aggregate`` round-trip rather than
        scaling linearly with the active-task count.

        Returns:
            A :class:`LiveActivitySnapshot` carrying per-agent
            activity rows plus aggregate stuck/runaway markers and
            total cost.
        """
        runaway_pct = runaway_cost_percent
        now = self._clock.now()
        stuck_cutoff = now - timedelta(minutes=stuck_idle_minutes)

        activities: list[AgentActivity] = []
        for status in _ACTIVE_STATUSES:
            tasks, _ = await self._task_engine.list_tasks(status=status)
            if not tasks:
                continue
            try:
                async with asyncio.TaskGroup() as tg:
                    handles = [
                        tg.create_task(
                            self._build_activity(task, stuck_cutoff, runaway_pct)
                        )
                        for task in tasks
                    ]
                activities.extend(handle.result() for handle in handles)
            except* (MemoryError, RecursionError) as fatal_eg:
                reraise_critical_unwrapped(fatal_eg)
            except* Exception as eg:  # noqa: BLE001 -- re-raised by _fail_snapshot
                _fail_snapshot(eg, status=status)

        stuck = tuple(NotBlankStr(a.agent_id) for a in activities if a.is_stuck)
        runaway = tuple(NotBlankStr(a.agent_id) for a in activities if a.is_runaway)
        snapshot = LiveActivitySnapshot(
            timestamp=now,
            agents=tuple(activities),
            total_cost=_sum_costs(a.cost for a in activities),
            active_count=len(activities),
            stuck_agents=stuck,
            runaway_agents=runaway,
        )
        logger.info(
            COCKPIT_SNAPSHOT_PUBLISHED,
            active_count=snapshot.active_count,
            stuck_count=len(stuck),
            runaway_count=len(runaway),
        )
        for agent_id in stuck:
            logger.warning(COCKPIT_STUCK_DETECTED, agent_id=agent_id)
        for agent_id in runaway:
            logger.warning(COCKPIT_RUNAWAY_DETECTED, agent_id=agent_id)
        return snapshot

    async def _recorded_cost_for(self, task: Task, execution_id: str | None) -> float:
        """Return what the frame store already holds for one execution.

        Scoped to the task as well as the execution because the aggregate it
        is subtracted from is task-scoped, and an execution id that somehow
        reached another task's frames must not deduct spend this task never
        had.

        Returns:
            The recorded cost for *execution_id*, or ``0.0`` when the live
            row names no execution and there is nothing to deduct.
        """
        if execution_id is None:
            return 0.0
        recorded = await self._frames.get_aggregate(
            FlightRecorderFrameFilterSpec(
                task_id=NotBlankStr(task.id),
                execution_id=NotBlankStr(execution_id),
            ),
        )
        return recorded.total_cost

    async def _build_activity(
        self,
        task: Task,
        stuck_cutoff: datetime,
        runaway_pct: float,
    ) -> AgentActivity:
        """Derive one task's activity row, live state first.

        Two stores answer "what is this agent doing", and which one is
        authoritative depends on whether the run is still going. The live
        ``AgentRuntimeState`` row is written per turn, so it is the answer
        while the agent still holds this task. The flight-recorder aggregate
        is built from a finished run, so it is the answer afterwards, and it
        is a single ``get_aggregate`` round-trip covering the whole frame
        history rather than a bounded page (both to avoid an N+1 across
        active tasks and so the cost does not cap at the page window).

        Reading only the frames is what made this surface blind: a run in
        flight has no frames yet, so every live row read ``turn_count=0``,
        ``cost=0``, ``last_active=None``.

        Cost while a run is live is the recorded executions plus the one in
        flight. The live row counts only THIS execution's spend while the
        runaway check compares against a per-task budget, so a retry starting
        from zero would let a task that already burned most of its budget read
        healthy for the whole of its next attempt and flip the moment that
        attempt ended. The live execution is deducted from the recorded side
        because an attempt's frames are written before its live row is
        cleared, and between those two writes both describe the same spend:
        a brief window when a run ends cleanly, an unbounded one when it does
        not, since a row left EXECUTING keeps its cost beside frames that
        already hold it and the doubled figure reads as a runaway that is not
        happening.

        That deduction is floored because the two frame reads are not one
        snapshot. A batch landing between them is counted only by the second,
        and a retention purge between them drops rows only from the second, so
        either can leave the execution-scoped figure larger than the task-wide
        total it comes off. ``AgentActivity.cost`` is ``ge=0`` and these rows
        build inside a ``TaskGroup``, so an inverted pair would abort the whole
        snapshot rather than under-report the one row it concerns.

        No activity at all is the STRONGEST evidence of stuck rather than an
        exemption from the check, so the task's own filing time is the
        fallback baseline: an in-flight task nothing has driven since a
        restart has no last-active timestamp, and requiring one reads every
        one of them as healthy. Filing time measures the QUEUE, though, which
        would read a task that waited behind earlier waves as stuck the moment
        it started. What keeps that honest is the row written at dispatch
        (``mark_agent_running``): a running task has a live timestamp from the
        moment it is picked up, so filing time is only ever consulted for a
        task no run has claimed, which is the case it describes.

        Returns:
            An :class:`AgentActivity` carrying the agent id, execution
            id, turn count, last-active timestamp, cumulative cost,
            and stuck / runaway flags derived from ``stuck_cutoff``
            and ``runaway_pct``.
        """
        agent_id = task.assigned_to or "unassigned"
        aggregate = await self._frames.get_aggregate(
            FlightRecorderFrameFilterSpec(task_id=NotBlankStr(task.id)),
        )
        live = await self._live_state(task)
        if live is not None:
            turn_count = live.turn_count
            last_active: datetime | None = live.last_activity_at
            execution_id: str | None = live.execution_id
            # Floored because the two frame reads are not one snapshot, and a
            # negative cost takes down the snapshot rather than just this row.
            recorded_here = await self._recorded_cost_for(task, live.execution_id)
            cost = max(0.0, aggregate.total_cost - recorded_here) + (
                live.accumulated_cost
            )
        else:
            turn_count = aggregate.max_turn_index
            last_active = aggregate.latest_timestamp
            execution_id = aggregate.latest_execution_id
            cost = aggregate.total_cost
        # No activity at all is evidence of stuck, not an exemption from it.
        is_stuck = (last_active or task.created_at) < stuck_cutoff
        is_runaway = task.budget_limit > 0 and cost > task.budget_limit * (
            runaway_pct / _PERCENT_DIVISOR
        )
        return AgentActivity(
            agent_id=NotBlankStr(agent_id),
            task_id=NotBlankStr(task.id),
            task_title=NotBlankStr(task.title),
            execution_id=None if execution_id is None else NotBlankStr(execution_id),
            status=task.status,
            turn_count=turn_count,
            cost=cost,
            last_active=last_active,
            is_stuck=is_stuck,
            is_runaway=is_runaway,
        )

    async def _live_state(self, task: Task) -> AgentRuntimeState | None:
        """Return the agent's live state when it is running THIS task.

        The row is keyed by agent, so a state naming another task belongs to
        a different run and says nothing about this one; an IDLE row says the
        agent has stopped, and the recorded frames are then the answer.

        Returns:
            The live state, or ``None`` when there is no store, no assignee,
            no row, or the row is not about this task.
        """
        if self._agent_states is None or task.assigned_to is None:
            return None
        state = await self._agent_states.get(NotBlankStr(task.assigned_to))
        if state is None or state.status is ExecutionStatus.IDLE:
            return None
        if state.task_id != str(task.id):
            return None
        return state
