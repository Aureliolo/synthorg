"""Shared trust-criteria predicates.

The milestone and per-category trust strategies both gate promotion on
the same "best single-window task count + quality floor" rule. It lives
here once so the two strategies cannot drift apart on what counts as
meeting a threshold.
"""

from synthorg.hr.performance.models import AgentPerformanceSnapshot


def meets_tasks_and_quality(
    snapshot: AgentPerformanceSnapshot,
    *,
    tasks_completed_min: int,
    quality_score_min: float,
) -> bool:
    """Whether the snapshot meets the task-count and quality thresholds.

    Uses the best single-window task count (not the cumulative total) so
    a sustained burst, not lifetime volume, satisfies the bar. A missing
    overall quality score fails only when a positive quality floor is
    required.

    Args:
        snapshot: The agent performance snapshot.
        tasks_completed_min: Minimum best-single-window task count.
        quality_score_min: Minimum overall quality score (``0.0`` waives
            the quality requirement).

    Returns:
        ``True`` when both thresholds are met.
    """
    max_tasks_completed = max(
        (window.tasks_completed for window in snapshot.windows),
        default=0,
    )
    if max_tasks_completed < tasks_completed_min:
        return False
    quality = snapshot.overall_quality_score
    if quality is not None and quality < quality_score_min:
        return False
    return not (quality is None and quality_score_min > 0.0)
