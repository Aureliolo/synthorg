"""Shared test helpers for ceremony scheduling strategy tests."""

from synthorg.engine.workflow.ceremony_context import CeremonyEvalContext
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus

SECONDS_PER_DAY: float = 86_400.0


def make_sprint(  # noqa: PLR0913
    task_count: int = 10,
    completed_count: int = 0,
    status: SprintStatus = SprintStatus.ACTIVE,
    duration_days: int = 14,
    story_points_committed: float | None = None,
    story_points_completed: float | None = None,
) -> Sprint:
    """Create a sprint with the given task/completion counts.

    Story points default to ``task_count * 3`` (committed) and
    ``completed_count * 3`` (completed) when not provided.
    """
    task_ids = tuple(f"task-{i}" for i in range(task_count))
    completed_ids = tuple(f"task-{i}" for i in range(completed_count))
    committed = (
        story_points_committed
        if story_points_committed is not None
        else float(task_count * 3)
    )
    completed_pts = (
        story_points_completed
        if story_points_completed is not None
        else float(completed_count * 3)
    )
    kwargs: dict[str, object] = {
        "id": "sprint-1",
        "name": "Sprint 1",
        "sprint_number": 1,
        "status": status,
        "duration_days": duration_days,
        "task_ids": task_ids,
        "completed_task_ids": completed_ids,
        "story_points_committed": committed,
        "story_points_completed": completed_pts,
    }
    if status is not SprintStatus.PLANNING:
        kwargs["start_date"] = "2026-04-01T00:00:00"
    if status is SprintStatus.COMPLETED:
        kwargs["end_date"] = "2026-04-14T00:00:00"
    return Sprint.model_validate(kwargs)


def make_context(  # noqa: PLR0913
    elapsed_seconds: float = 0.0,
    completions_since_last: int = 0,
    total_completions: int = 0,
    total_tasks: int = 10,
    sprint_pct: float = 0.0,
    story_points_committed: float | None = None,
    story_points_completed: float | None = None,
    budget_consumed_fraction: float = 0.0,
    budget_remaining: float = 0.0,
    external_events: tuple[str, ...] = (),
) -> CeremonyEvalContext:
    """Create an evaluation context.

    Story points default to ``sprint_pct * total_tasks * 3`` (completed)
    and ``total_tasks * 3`` (committed) when not provided.
    """
    committed = (
        story_points_committed
        if story_points_committed is not None
        else float(total_tasks * 3)
    )
    completed_pts = (
        story_points_completed
        if story_points_completed is not None
        else sprint_pct * total_tasks * 3
    )
    return CeremonyEvalContext(
        completions_since_last_trigger=completions_since_last,
        total_completions_this_sprint=total_completions,
        total_tasks_in_sprint=total_tasks,
        elapsed_seconds=elapsed_seconds,
        budget_consumed_fraction=budget_consumed_fraction,
        budget_remaining=budget_remaining,
        velocity_history=(),
        external_events=external_events,
        sprint_percentage_complete=sprint_pct,
        story_points_completed=completed_pts,
        story_points_committed=committed,
    )
