"""Live org-activity aggregation for the mission-control cockpit.

Builds a :class:`LiveActivitySnapshot` of in-flight work: who is working
on what, accumulated cost per agent, and stuck / runaway flags derived
from operator-tuned thresholds. Activity and idle time come from the
flight-recorder frames; cost comes from the cost tracker when wired.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.cockpit import (
    COCKPIT_RUNAWAY_DETECTED,
    COCKPIT_SNAPSHOT_PUBLISHED,
    COCKPIT_STUCK_DETECTED,
)
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrameFilterSpec,
)

if TYPE_CHECKING:
    from synthorg.core.task import Task
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.persistence.flight_recorder_protocol import (
        FlightRecorderFrameRepository,
    )
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_COCKPIT_NS: Final[str] = "cockpit"
_STUCK_KEY: Final[str] = "stuck_idle_threshold_minutes"
_RUNAWAY_KEY: Final[str] = "runaway_cost_threshold_percent"
_PERCENT_DIVISOR: Final[float] = 100.0
_ACTIVE_STATUSES: Final[tuple[TaskStatus, ...]] = (
    TaskStatus.IN_PROGRESS,
    TaskStatus.BLOCKED,
)
#: Bounded page when summing cost from frames without a cost tracker.
_FRAME_COST_PAGE: Final[int] = 1000


class AgentActivity(BaseModel):
    """One agent's in-flight activity in the live snapshot."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent working the task")
    task_id: NotBlankStr = Field(description="Task being worked")
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


class CockpitService:
    """Aggregates in-flight work into a live activity snapshot."""

    def __init__(
        self,
        task_engine: TaskEngine,
        flight_recorder_frames: FlightRecorderFrameRepository,
        *,
        config_resolver: ConfigResolver,
        clock: Clock | None = None,
    ) -> None:
        self._task_engine = task_engine
        self._frames = flight_recorder_frames
        self._config_resolver = config_resolver
        self._clock = clock or SystemClock()

    async def get_live_snapshot(self) -> LiveActivitySnapshot:
        """Build a snapshot of active work with stuck / runaway flags."""
        stuck_minutes = await self._config_resolver.get_float(_COCKPIT_NS, _STUCK_KEY)
        runaway_pct = await self._config_resolver.get_float(_COCKPIT_NS, _RUNAWAY_KEY)
        now = self._clock.now()
        stuck_cutoff = now - timedelta(minutes=stuck_minutes)

        activities: list[AgentActivity] = []
        for status in _ACTIVE_STATUSES:
            tasks, _ = await self._task_engine.list_tasks(status=status)
            activities.extend(
                [
                    await self._build_activity(task, stuck_cutoff, runaway_pct)
                    for task in tasks
                ]
            )

        stuck = tuple(NotBlankStr(a.agent_id) for a in activities if a.is_stuck)
        runaway = tuple(NotBlankStr(a.agent_id) for a in activities if a.is_runaway)
        snapshot = LiveActivitySnapshot(
            timestamp=now,
            agents=tuple(activities),
            total_cost=sum(a.cost for a in activities),
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

    async def _build_activity(
        self,
        task: Task,
        stuck_cutoff: AwareDatetime,
        runaway_pct: float,
    ) -> AgentActivity:
        """Derive one task's activity row from frames + cost tracker."""
        agent_id = task.assigned_to or "unassigned"
        frames = await self._frames.query(
            FlightRecorderFrameFilterSpec(task_id=NotBlankStr(task.id)),
            limit=_FRAME_COST_PAGE,
        )
        latest = frames[0] if frames else None
        turn_count = latest.turn_index if latest is not None else 0
        last_active = latest.timestamp if latest is not None else None
        cost = sum(frame.cost for frame in frames)
        is_stuck = last_active is not None and last_active < stuck_cutoff
        is_runaway = task.budget_limit > 0 and cost > task.budget_limit * (
            runaway_pct / _PERCENT_DIVISOR
        )
        return AgentActivity(
            agent_id=NotBlankStr(agent_id),
            task_id=NotBlankStr(task.id),
            status=task.status,
            turn_count=turn_count,
            cost=cost,
            last_active=last_active,
            is_stuck=is_stuck,
            is_runaway=is_runaway,
        )
