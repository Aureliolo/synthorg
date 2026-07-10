# module-kind: code
"""Department health outcome derivation.

Pure + tracker-backed helpers that turn a department's real terminal task
runs into an honest health signal (success rate + a 0-100 score) with an
explicit no-data gate. Extracted from ``_department_health`` to keep that
aggregator within its module-size budget.
"""

from datetime import datetime

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_REQUEST_ERROR
from synthorg.observability.events.performance import (
    PERF_TASK_OUTCOMES_TRACKER_UNWIRED,
)

logger = get_logger(__name__)


def health_from_outcomes(
    total_runs: int, success_count: int, min_runs: int
) -> float | None:
    """Derive the task-outcome success rate with an explicit no-data gate.

    Returns ``None`` below ``min_runs`` so a department without enough activity
    reads as no-data rather than a fabricated full-health number. The 0-100
    ``health_score`` is derived from this rate as a ``computed_field`` on
    :class:`DepartmentHealth`.

    Returns:
        ``success_rate`` in ``[0, 1]``, or ``None`` below the gate.
    """
    if total_runs <= 0 or total_runs < min_runs:
        return None
    return round(success_count / total_runs, 4)


def resolve_task_outcomes(
    app_state: AppState,
    agent_ids: tuple[str, ...],
    *,
    window_start: datetime,
) -> tuple[int, int]:
    """Count terminal runs and successes for the department's agents.

    Reads the recorded task-outcome metrics (one per terminal run) for each
    department agent within the health window. Every ``TaskMetricRecord`` is a
    terminal run, so the total is the run count and the second value the count
    that genuinely produced output (empty and failed runs are recorded as
    non-success).

    Returns:
        ``(total_runs, success_count)``. ``(0, 0)`` means no signal, from one
        of three distinct causes: no department agents (silent), the
        performance tracker is unwired (logged at DEBUG), or every agent's
        query failed (each logged at WARNING). A fault reading one agent is
        caught per-agent, so the real counts from the agents that were
        readable are still returned rather than discarded.
    """
    tracker = app_state.slice(HrStateSlice).performance_tracker
    if tracker is None:
        logger.debug(
            PERF_TASK_OUTCOMES_TRACKER_UNWIRED,
            endpoint="departments.health.task_outcomes",
            agent_count=len(agent_ids),
        )
        return 0, 0
    if not agent_ids:
        return 0, 0
    total = 0
    success = 0
    for agent_id in agent_ids:
        try:
            metrics = tracker.get_task_metrics(
                agent_id=NotBlankStr(agent_id), since=window_start
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_REQUEST_ERROR,
                endpoint="departments.health.task_outcomes",
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            continue
        for record in metrics:
            total += 1
            if record.is_success:
                success += 1
    return total, success
