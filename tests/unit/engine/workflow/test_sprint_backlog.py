"""Tests for sprint backlog assembly functions.

Recording a delivery is not tested here: it is a guarded repository write
(``complete_task_if``), covered by the dual-backend conformance suite,
because the guard it depends on is the database's and an in-memory
rehearsal of it would assert nothing about the thing that runs.
"""

import pytest

from synthorg.engine.workflow.sprint_backlog import add_task_to_sprint
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
