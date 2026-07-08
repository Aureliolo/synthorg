"""Tests for sprint backlog management functions."""

import pytest

from synthorg.engine.workflow.sprint_backlog import (
    add_task_to_sprint,
    complete_task_in_sprint,
    remove_task_from_sprint,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus


def _planning_sprint(**overrides: object) -> Sprint:
    defaults: dict[str, object] = {
        "id": "sprint-1",
        "name": "Sprint 1",
        "sprint_number": 1,
        "status": SprintStatus.PLANNING,
    }
    defaults.update(overrides)
    return Sprint(**defaults)  # type: ignore[arg-type]


def _active_sprint(**overrides: object) -> Sprint:
    defaults: dict[str, object] = {
        "id": "sprint-1",
        "name": "Sprint 1",
        "sprint_number": 1,
        "status": SprintStatus.ACTIVE,
        "start_date": "2026-04-01",
        "task_ids": ("t-1", "t-2", "t-3"),
        "task_points": {"t-1": 5.0, "t-2": 3.0, "t-3": 5.0},
        "story_points_committed": 13.0,
    }
    defaults.update(overrides)
    return Sprint(**defaults)  # type: ignore[arg-type]


# ── add_task_to_sprint ─────────────────────────────────────────


class TestAddTaskToSprint:
    """add_task_to_sprint adds tasks during PLANNING only."""

    @pytest.mark.unit
    def test_add_task(self) -> None:
        sprint = _planning_sprint()
        result = add_task_to_sprint(sprint, "t-1", story_points=3.0)
        assert "t-1" in result.task_ids
        assert result.story_points_committed == 3.0
        assert result.task_points["t-1"] == 3.0

    @pytest.mark.unit
    def test_add_multiple_tasks(self) -> None:
        sprint = _planning_sprint()
        sprint = add_task_to_sprint(sprint, "t-1", story_points=3.0)
        sprint = add_task_to_sprint(sprint, "t-2", story_points=5.0)
        assert sprint.task_ids == ("t-1", "t-2")
        assert sprint.story_points_committed == 8.0

    @pytest.mark.unit
    def test_original_unchanged(self) -> None:
        sprint = _planning_sprint()
        add_task_to_sprint(sprint, "t-1", story_points=3.0)
        assert sprint.task_ids == ()
        assert sprint.story_points_committed == 0.0

    @pytest.mark.unit
    def test_reject_when_active(self) -> None:
        sprint = _active_sprint()
        with pytest.raises(ValueError, match="must be 'planning'"):
            add_task_to_sprint(sprint, "t-new")

    @pytest.mark.unit
    def test_reject_duplicate_task(self) -> None:
        sprint = _planning_sprint(task_ids=("t-1",))
        with pytest.raises(ValueError, match="already in sprint"):
            add_task_to_sprint(sprint, "t-1")

    @pytest.mark.unit
    def test_reject_negative_story_points(self) -> None:
        sprint = _planning_sprint()
        with pytest.raises(ValueError, match="story_points"):
            add_task_to_sprint(sprint, "t-1", story_points=-1.0)

    @pytest.mark.unit
    def test_zero_story_points_allowed(self) -> None:
        sprint = _planning_sprint()
        result = add_task_to_sprint(sprint, "t-1", story_points=0.0)
        assert "t-1" in result.task_ids


# ── remove_task_from_sprint ────────────────────────────────────


class TestRemoveTaskFromSprint:
    """remove_task_from_sprint removes tasks (not from COMPLETED)."""

    @pytest.mark.unit
    def test_remove_from_planning(self) -> None:
        sprint = _planning_sprint(task_ids=("t-1", "t-2"))
        result = remove_task_from_sprint(sprint, "t-1")
        assert result.task_ids == ("t-2",)

    @pytest.mark.unit
    def test_remove_from_active(self) -> None:
        sprint = _active_sprint()
        result = remove_task_from_sprint(sprint, "t-2")
        assert "t-2" not in result.task_ids

    @pytest.mark.unit
    def test_removes_from_completed_task_ids_too(self) -> None:
        sprint = _active_sprint(
            completed_task_ids=("t-1",),
            story_points_committed=13.0,
            story_points_completed=5.0,
        )
        result = remove_task_from_sprint(sprint, "t-1")
        assert "t-1" not in result.completed_task_ids
        assert "t-1" not in result.task_ids
        assert "t-1" not in result.task_points

    @pytest.mark.unit
    def test_reclaims_points_on_removal(self) -> None:
        sprint = _active_sprint(
            completed_task_ids=("t-1",),
            story_points_committed=13.0,
            story_points_completed=5.0,
        )
        result = remove_task_from_sprint(sprint, "t-1")
        assert result.story_points_committed == 8.0
        assert result.story_points_completed == 0.0

    @pytest.mark.unit
    def test_reject_from_completed_sprint(self) -> None:
        sprint = Sprint(
            id="sprint-1",
            name="Sprint 1",
            sprint_number=1,
            status=SprintStatus.COMPLETED,
            start_date="2026-04-01",
            end_date="2026-04-14",
            task_ids=("t-1",),
        )
        with pytest.raises(ValueError, match="completed sprint"):
            remove_task_from_sprint(sprint, "t-1")

    @pytest.mark.unit
    def test_reject_unknown_task(self) -> None:
        sprint = _active_sprint()
        with pytest.raises(ValueError, match="not in sprint"):
            remove_task_from_sprint(sprint, "nonexistent")

    @pytest.mark.unit
    def test_original_unchanged(self) -> None:
        sprint = _active_sprint()
        remove_task_from_sprint(sprint, "t-1")
        assert "t-1" in sprint.task_ids


# ── complete_task_in_sprint ────────────────────────────────────


class TestCompleteTaskInSprint:
    """complete_task_in_sprint marks tasks done during ACTIVE/IN_REVIEW.

    Points credited come from the sprint's per-task ``task_points`` (what
    the task committed when added), never a caller-supplied value.
    """

    @pytest.mark.unit
    def test_complete_task_credits_committed_points(self) -> None:
        sprint = _active_sprint()
        result = complete_task_in_sprint(sprint, "t-1")
        assert "t-1" in result.completed_task_ids
        assert result.story_points_completed == 5.0

    @pytest.mark.unit
    def test_complete_multiple_tasks(self) -> None:
        sprint = _active_sprint()
        sprint = complete_task_in_sprint(sprint, "t-1")
        sprint = complete_task_in_sprint(sprint, "t-2")
        assert sprint.completed_task_ids == ("t-1", "t-2")
        assert sprint.story_points_completed == 8.0

    @pytest.mark.unit
    def test_missing_points_entry_credits_zero(self) -> None:
        sprint = _active_sprint(task_points={"t-1": 5.0, "t-3": 5.0})
        result = complete_task_in_sprint(sprint, "t-2")
        assert "t-2" in result.completed_task_ids
        assert result.story_points_completed == 0.0

    @pytest.mark.unit
    def test_reject_when_planning(self) -> None:
        sprint = _planning_sprint(
            task_ids=("t-1",),
            task_points={"t-1": 5.0},
            story_points_committed=5.0,
        )
        with pytest.raises(ValueError, match="must be 'active' or 'in_review'"):
            complete_task_in_sprint(sprint, "t-1")

    @pytest.mark.unit
    def test_reject_unknown_task(self) -> None:
        sprint = _active_sprint()
        with pytest.raises(ValueError, match="not in sprint"):
            complete_task_in_sprint(sprint, "unknown")

    @pytest.mark.unit
    def test_reject_already_completed(self) -> None:
        sprint = _active_sprint(
            completed_task_ids=("t-1",),
            story_points_completed=5.0,
        )
        with pytest.raises(ValueError, match="already completed"):
            complete_task_in_sprint(sprint, "t-1")

    @pytest.mark.unit
    def test_in_review_status_allowed(self) -> None:
        sprint = Sprint(
            id="sprint-1",
            name="Sprint 1",
            sprint_number=1,
            status=SprintStatus.IN_REVIEW,
            start_date="2026-04-01",
            task_ids=("t-1",),
            task_points={"t-1": 5.0},
            story_points_committed=5.0,
        )
        result = complete_task_in_sprint(sprint, "t-1")
        assert "t-1" in result.completed_task_ids

    @pytest.mark.unit
    def test_original_unchanged(self) -> None:
        sprint = _active_sprint()
        complete_task_in_sprint(sprint, "t-1")
        assert sprint.completed_task_ids == ()
        assert sprint.story_points_completed == 0.0
