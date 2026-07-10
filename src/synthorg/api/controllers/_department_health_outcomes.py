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

logger = get_logger(__name__)


def health_from_outcomes(
    total_runs: int, success_count: int, min_runs: int
) -> tuple[float | None, float | None]:
    """Derive ``(success_rate, health_score)`` with an explicit no-data gate.

    Returns ``(None, None)`` below ``min_runs`` so a department without enough
    activity reads as no-data rather than a fabricated full-health number.

    Returns:
        ``(success_rate 0-1, health_score 0-100)`` or ``(None, None)``.
    """
    if total_runs <= 0 or total_runs < min_runs:
        return None, None
    rate = success_count / total_runs
    return round(rate, 4), round(rate * 100, 2)


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
        ``(total_runs, success_count)``; ``(0, 0)`` when the performance
        tracker is unwired or the query fails.
    """
    tracker = app_state.slice(HrStateSlice).performance_tracker
    if tracker is None or not agent_ids:
        return 0, 0
    try:
        total = 0
        success = 0
        for agent_id in agent_ids:
            for record in tracker.get_task_metrics(
                agent_id=NotBlankStr(agent_id), since=window_start
            ):
                total += 1
                if record.is_success:
                    success += 1
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="departments.health.task_outcomes",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return 0, 0
    return total, success
