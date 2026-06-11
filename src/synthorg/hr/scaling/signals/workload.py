"""Workload signal source -- reads agent utilization from assignment."""

from datetime import UTC, datetime
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.engine.assignment.models import AgentWorkload
from synthorg.hr.scaling.models import ScalingSignal
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_SIGNAL_COLLECTION_DEGRADED

logger = get_logger(__name__)
_DEFAULT_MAX_CONCURRENT_TASKS: Final[int] = 3

_SOURCE_NAME = NotBlankStr("workload")


class WorkloadSignalSource:
    """Read-only adapter over the assignment subsystem.

    Converts ``AgentWorkload`` snapshots into scaling signals:
    ``avg_utilization``, ``peak_utilization``, ``queue_depth``.

    Args:
        max_concurrent_tasks: Maximum concurrent tasks per agent
            (used to compute utilization as a fraction).
    """

    def __init__(
        self,
        *,
        max_concurrent_tasks: int = _DEFAULT_MAX_CONCURRENT_TASKS,
    ) -> None:
        if max_concurrent_tasks <= 0:
            msg = "max_concurrent_tasks must be > 0"
            raise ValueError(msg)
        self._max_concurrent = max_concurrent_tasks

    @property
    def name(self) -> NotBlankStr:
        """Source identifier."""
        return _SOURCE_NAME

    async def collect(
        self,
        agent_ids: tuple[NotBlankStr, ...],
        *,
        workloads: tuple[AgentWorkload, ...] = (),
    ) -> tuple[ScalingSignal, ...]:
        """Collect workload signals from agent workload snapshots.

        Args:
            agent_ids: Active agent IDs (used for queue depth).
            workloads: Current workload snapshots per agent.

        Returns:
            Workload signals: avg_utilization, peak_utilization,
            queue_depth.
        """
        now = datetime.now(UTC)
        active_set = set(agent_ids)
        workloads = tuple(w for w in workloads if w.agent_id in active_set)

        if not workloads:
            if agent_ids:
                logger.warning(
                    HR_SCALING_SIGNAL_COLLECTION_DEGRADED,
                    source="workload",
                    reason="no_workloads_for_active_agents",
                    agent_count=len(agent_ids),
                )
            return (
                ScalingSignal(
                    name=NotBlankStr("avg_utilization"),
                    value=0.0,
                    source=_SOURCE_NAME,
                    timestamp=now,
                ),
                ScalingSignal(
                    name=NotBlankStr("peak_utilization"),
                    value=0.0,
                    source=_SOURCE_NAME,
                    timestamp=now,
                ),
                ScalingSignal(
                    name=NotBlankStr("queue_depth"),
                    value=0.0,
                    source=_SOURCE_NAME,
                    timestamp=now,
                ),
            )

        utilizations = tuple(
            min(w.active_task_count / self._max_concurrent, 1.0) for w in workloads
        )
        avg_util = sum(utilizations) / len(utilizations)
        peak_util = max(utilizations)
        total_tasks = sum(w.active_task_count for w in workloads)

        return (
            ScalingSignal(
                name=NotBlankStr("avg_utilization"),
                value=round(avg_util, 4),
                source=_SOURCE_NAME,
                timestamp=now,
            ),
            ScalingSignal(
                name=NotBlankStr("peak_utilization"),
                value=round(peak_util, 4),
                source=_SOURCE_NAME,
                timestamp=now,
            ),
            ScalingSignal(
                name=NotBlankStr("queue_depth"),
                value=float(total_tasks),
                source=_SOURCE_NAME,
                timestamp=now,
            ),
        )
