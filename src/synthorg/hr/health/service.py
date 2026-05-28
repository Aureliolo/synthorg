"""Agent health aggregation service.

Derives a compact health verdict per agent from the existing
:class:`PerformanceTracker` snapshot:

- ``healthy`` when recent success rate is above
  :data:`_DEGRADED_THRESHOLD` (or no signal exists yet -- new agents
  default to healthy rather than degraded, since the degradation
  signal requires at least one completed task);
- ``degraded`` when recent success rate is between
  :data:`_UNAVAILABLE_THRESHOLD` and :data:`_DEGRADED_THRESHOLD`;
- ``unavailable`` when recent success rate is at or below
  :data:`_UNAVAILABLE_THRESHOLD`.

The "recent" window is the tightest one the tracker produced
(typically ``7d`` in standard config); the service picks the shortest
available window so health reacts quickly to regressions without
constantly redefining "recent" on its own.
"""

from typing import TYPE_CHECKING, Final, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.hr import (
    HR_AGENT_HEALTH_COMPUTED,
    HR_AGENT_HEALTH_FAILED,
)

if TYPE_CHECKING:
    from synthorg.hr.performance.models import (
        AgentPerformanceSnapshot,
        WindowMetrics,
    )
    from synthorg.hr.performance.tracker import PerformanceTracker


logger = get_logger(__name__)


HealthStatus = Literal["healthy", "degraded", "unavailable"]

_DEGRADED_THRESHOLD: Final[float] = 0.8
_UNAVAILABLE_THRESHOLD: Final[float] = 0.5


class AgentHealthReport(BaseModel):
    """Compact health verdict derived from a performance snapshot.

    Attributes:
        agent_id: The agent being evaluated.
        status: ``"healthy"`` / ``"degraded"`` / ``"unavailable"``.
        computed_at: When this report was computed (mirrors
            ``AgentPerformanceSnapshot.computed_at``).
        recent_window: Window label that backed the verdict (e.g.
            ``"7d"``); ``None`` when no window had any data points.
        recent_success_rate: Success rate (0.0-1.0) in
            ``recent_window``; ``None`` when no signal is available.
        recent_task_count: Task count in ``recent_window``.
        recent_failed_count: Failed-task count in ``recent_window``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Agent being evaluated")
    status: HealthStatus = Field(description="Derived health verdict")
    computed_at: AwareDatetime = Field(description="When this report was computed")
    recent_window: NotBlankStr | None = Field(
        default=None,
        description="Window label backing the verdict",
    )
    recent_success_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Success rate in the recent window",
    )
    recent_task_count: int = Field(
        default=0,
        ge=0,
        description="Completed + failed task count in the recent window",
    )
    recent_failed_count: int = Field(
        default=0,
        ge=0,
        description="Failed-task count in the recent window",
    )

    @model_validator(mode="after")
    def _validate_counts(self) -> Self:
        """Reject reports with inconsistent counts or window/rate coupling.

        Two invariants:

        1. ``recent_failed_count <= recent_task_count`` -- the verdict
           logic derives status from the success rate, which would be
           negative if failed exceeded total.
        2. ``recent_window`` and ``recent_success_rate`` are either
           both present or both absent. The factory enforces this by
           construction (both set together when a populated window
           exists, both left at ``None`` otherwise), but the
           invariant is part of the public contract -- a caller that
           mints an ``AgentHealthReport`` directly should not be
           able to produce an inconsistent "rate-without-window" or
           "window-without-rate" report.

        Enforcing both at construction time means any report handed
        to a handler is already well-formed.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.recent_failed_count > self.recent_task_count:
            msg = (
                f"recent_failed_count ({self.recent_failed_count}) cannot "
                f"exceed recent_task_count ({self.recent_task_count})"
            )
            raise ValueError(msg)
        window_set = self.recent_window is not None
        rate_set = self.recent_success_rate is not None
        if window_set != rate_set:
            msg = (
                "recent_window and recent_success_rate must both be set or "
                f"both be None (got window={self.recent_window!r}, "
                f"rate={self.recent_success_rate!r})"
            )
            raise ValueError(msg)
        return self


class AgentHealthService:
    """Derives :class:`AgentHealthReport` from performance snapshots."""

    __slots__ = ("_performance_tracker",)

    def __init__(
        self,
        *,
        performance_tracker: PerformanceTracker,
    ) -> None:
        """Initialize the service with the performance tracker dependency.

        Args:
            performance_tracker: The tracker that holds rolling
                task-metric windows per agent.
        """
        self._performance_tracker = performance_tracker

    async def get_agent_health(
        self,
        agent_id: NotBlankStr,
    ) -> AgentHealthReport:
        """Compute a compact health verdict for *agent_id*.

        Args:
            agent_id: Agent to evaluate.

        Returns:
            A health report with status + the window that backed it.

        Raises:
            Exception: Re-raises whatever the snapshot fetch or report
                construction raises, after logging a structured
                failure event so the error is never silent outside
                the handler layer.
        """
        try:
            snapshot = await self._performance_tracker.get_snapshot(agent_id)
            report = _report_from_snapshot(agent_id, snapshot)
        except Exception as exc:
            log_exception_redacted(
                logger,
                HR_AGENT_HEALTH_FAILED,
                exc,
                agent_id=agent_id,
                stage="snapshot" if "snapshot" not in locals() else "report",
            )
            raise
        logger.info(
            HR_AGENT_HEALTH_COMPUTED,
            agent_id=agent_id,
            status=report.status,
            recent_window=report.recent_window,
            recent_task_count=report.recent_task_count,
        )
        return report


def _report_from_snapshot(
    agent_id: NotBlankStr,
    snapshot: AgentPerformanceSnapshot,
) -> AgentHealthReport:
    """Collapse a full performance snapshot into a health report.

    Returns:
        Result of type ``AgentHealthReport``.
    """
    window = _pick_recent_window(snapshot.windows)
    if window is None or window.success_rate is None:
        return AgentHealthReport(
            agent_id=agent_id,
            status="healthy",
            computed_at=snapshot.computed_at,
        )
    task_count = window.tasks_completed + window.tasks_failed
    return AgentHealthReport(
        agent_id=agent_id,
        status=_verdict(window.success_rate),
        computed_at=snapshot.computed_at,
        recent_window=window.window_size,
        recent_success_rate=window.success_rate,
        recent_task_count=task_count,
        recent_failed_count=window.tasks_failed,
    )


def _pick_recent_window(
    windows: tuple[WindowMetrics, ...],
) -> WindowMetrics | None:
    """Return the tightest populated window ("most recent" signal).

    The tracker emits one window per configured rolling period (7d /
    30d / 90d / ...) in order from shortest to longest. For a
    "current health" verdict we want the smallest window that still
    has observations -- that's the one most responsive to recent
    regressions. Picking the window with the largest
    ``data_point_count`` (the previous behaviour) biased toward the
    longest horizon and masked a fresh dip behind months of older
    successes. We now iterate in the tracker's configured order and
    take the first populated window.

    Returns:
        The resulting ``WindowMetrics``, or ``None`` when unavailable.
    """
    for window in windows:
        if window.data_point_count > 0:
            return window
    return None


def _verdict(success_rate: float) -> HealthStatus:
    """Map a success rate onto a health status.

    Returns:
        Result of type ``HealthStatus``.
    """
    if success_rate <= _UNAVAILABLE_THRESHOLD:
        return "unavailable"
    if success_rate < _DEGRADED_THRESHOLD:
        return "degraded"
    return "healthy"


__all__ = ["AgentHealthReport", "AgentHealthService", "HealthStatus"]
