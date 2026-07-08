"""Tests for sprint lifecycle state machine and Sprint model."""

import pytest

from synthorg.engine.workflow.sprint_lifecycle import (
    VALID_SPRINT_TRANSITIONS,
    Sprint,
    SprintStatus,
    validate_sprint_transition,
)

# ── SprintStatus enum ─────────────────────────────────────────


class TestSprintStatusEnum:
    """SprintStatus has exactly five members."""

    @pytest.mark.unit
    def test_member_count(self) -> None:
        assert len(SprintStatus) == 5

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("member", "value"),
        [
            (SprintStatus.PLANNING, "planning"),
            (SprintStatus.ACTIVE, "active"),
            (SprintStatus.IN_REVIEW, "in_review"),
            (SprintStatus.RETROSPECTIVE, "retrospective"),
            (SprintStatus.COMPLETED, "completed"),
        ],
    )
    def test_member_values(self, member: SprintStatus, value: str) -> None:
        assert member.value == value


# ── Sprint lifecycle transitions ───────────────────────────────


class TestSprintTransitions:
    """validate_sprint_transition enforces linear lifecycle."""

    @pytest.mark.unit
    def test_every_status_has_transition_entry(self) -> None:
        for status in SprintStatus:
            assert status in VALID_SPRINT_TRANSITIONS

    @pytest.mark.unit
    def test_completed_is_terminal(self) -> None:
        assert VALID_SPRINT_TRANSITIONS[SprintStatus.COMPLETED] == frozenset()

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("from_s", "to_s"),
        [
            (SprintStatus.PLANNING, SprintStatus.ACTIVE),
            (SprintStatus.ACTIVE, SprintStatus.IN_REVIEW),
            (SprintStatus.IN_REVIEW, SprintStatus.RETROSPECTIVE),
            (SprintStatus.RETROSPECTIVE, SprintStatus.COMPLETED),
        ],
    )
    def test_valid_forward_transitions(
        self,
        from_s: SprintStatus,
        to_s: SprintStatus,
    ) -> None:
        validate_sprint_transition(from_s, to_s)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("from_s", "to_s"),
        [
            (SprintStatus.ACTIVE, SprintStatus.PLANNING),
            (SprintStatus.IN_REVIEW, SprintStatus.ACTIVE),
            (SprintStatus.RETROSPECTIVE, SprintStatus.IN_REVIEW),
            (SprintStatus.COMPLETED, SprintStatus.PLANNING),
            (SprintStatus.PLANNING, SprintStatus.COMPLETED),
            (SprintStatus.PLANNING, SprintStatus.RETROSPECTIVE),
        ],
    )
    def test_backward_and_skip_transitions_rejected(
        self,
        from_s: SprintStatus,
        to_s: SprintStatus,
    ) -> None:
        with pytest.raises(ValueError, match="Invalid sprint"):
            validate_sprint_transition(from_s, to_s)

    @pytest.mark.unit
    def test_self_transitions_rejected(self) -> None:
        for status in SprintStatus:
            with pytest.raises(ValueError, match="Invalid sprint"):
                validate_sprint_transition(status, status)

    @pytest.mark.unit
    def test_full_lifecycle_path(self) -> None:
        """Walk the full PLANNING -> COMPLETED path."""
        path = [
            SprintStatus.PLANNING,
            SprintStatus.ACTIVE,
            SprintStatus.IN_REVIEW,
            SprintStatus.RETROSPECTIVE,
            SprintStatus.COMPLETED,
        ]
        for i in range(len(path) - 1):
            validate_sprint_transition(path[i], path[i + 1])


# ── Sprint model validation ────────────────────────────────────


class TestSprintModel:
    """Sprint model validators enforce field constraints."""

    def _make_sprint(self, **overrides: object) -> Sprint:
        defaults: dict[str, object] = {
            "id": "sprint-1",
            "name": "Sprint 1",
            "sprint_number": 1,
        }
        defaults.update(overrides)
        return Sprint(**defaults)  # type: ignore[arg-type]

    @pytest.mark.unit
    def test_default_sprint(self) -> None:
        sprint = self._make_sprint()
        assert sprint.status == SprintStatus.PLANNING
        assert sprint.duration_days == 14
        assert sprint.task_ids == ()
        assert sprint.story_points_committed == 0.0

    @pytest.mark.unit
    def test_invalid_start_date_format(self) -> None:
        with pytest.raises(ValueError, match="ISO 8601"):
            self._make_sprint(start_date="not-a-date")

    @pytest.mark.unit
    def test_whitespace_start_date_rejected(self) -> None:
        with pytest.raises(ValueError, match="whitespace"):
            self._make_sprint(start_date="  ")

    @pytest.mark.unit
    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(ValueError, match="end_date"):
            self._make_sprint(
                start_date="2026-04-14",
                end_date="2026-04-01",
            )

    @pytest.mark.unit
    def test_valid_date_range(self) -> None:
        sprint = self._make_sprint(
            start_date="2026-04-01",
            end_date="2026-04-14",
        )
        assert sprint.start_date == "2026-04-01"
        assert sprint.end_date == "2026-04-14"

    @pytest.mark.unit
    def test_duplicate_task_ids_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"Duplicate.*task_ids"):
            self._make_sprint(task_ids=("t-1", "t-1"))

    @pytest.mark.unit
    def test_completed_task_not_in_backlog_rejected(self) -> None:
        with pytest.raises(ValueError, match="not in task_ids"):
            self._make_sprint(
                task_ids=("t-1",),
                completed_task_ids=("t-2",),
                story_points_committed=5.0,
            )

    @pytest.mark.unit
    def test_completed_exceeds_committed_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"exceeds.*committed"):
            self._make_sprint(
                story_points_committed=5.0,
                story_points_completed=10.0,
            )

    @pytest.mark.unit
    def test_task_points_stray_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="not in task_ids"):
            self._make_sprint(
                task_ids=("t-1",),
                task_points={"t-2": 3.0},
                story_points_committed=3.0,
            )

    @pytest.mark.unit
    def test_task_points_negative_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="negative values"):
            self._make_sprint(
                task_ids=("t-1",),
                task_points={"t-1": -1.0},
            )

    @pytest.mark.unit
    def test_task_points_valid(self) -> None:
        sprint = self._make_sprint(
            task_ids=("t-1", "t-2"),
            task_points={"t-1": 3.0, "t-2": 5.0},
            story_points_committed=8.0,
        )
        assert dict(sprint.task_points) == {"t-1": 3.0, "t-2": 5.0}

    @pytest.mark.unit
    def test_active_requires_start_date(self) -> None:
        with pytest.raises(ValueError, match="start_date is required"):
            self._make_sprint(
                status=SprintStatus.ACTIVE,
                start_date=None,
            )

    @pytest.mark.unit
    def test_completed_requires_end_date(self) -> None:
        with pytest.raises(ValueError, match="end_date is required"):
            self._make_sprint(
                status=SprintStatus.COMPLETED,
                start_date="2026-04-01",
                end_date=None,
            )

    @pytest.mark.unit
    def test_completed_sprint_valid(self) -> None:
        sprint = self._make_sprint(
            status=SprintStatus.COMPLETED,
            start_date="2026-04-01",
            end_date="2026-04-14",
            task_ids=("t-1", "t-2"),
            completed_task_ids=("t-1",),
            story_points_committed=8.0,
            story_points_completed=5.0,
        )
        assert sprint.status == SprintStatus.COMPLETED

    @pytest.mark.unit
    def test_duration_bounds(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal"):
            self._make_sprint(duration_days=0)
        with pytest.raises(ValueError, match="less than or equal"):
            self._make_sprint(duration_days=91)


# ── Sprint.with_transition ─────────────────────────────────────


class TestSprintWithTransition:
    """Sprint.with_transition validates and produces new instances."""

    def _make_planning_sprint(self) -> Sprint:
        return Sprint(
            id="sprint-1",
            name="Sprint 1",
            sprint_number=1,
        )

    @pytest.mark.unit
    def test_planning_to_active(self) -> None:
        sprint = self._make_planning_sprint()
        active = sprint.with_transition(
            SprintStatus.ACTIVE,
            start_date="2026-04-01",
        )
        assert active.status == SprintStatus.ACTIVE
        assert active.start_date == "2026-04-01"
        assert sprint.status == SprintStatus.PLANNING

    @pytest.mark.unit
    def test_invalid_transition_raises(self) -> None:
        sprint = self._make_planning_sprint()
        with pytest.raises(ValueError, match="Invalid sprint"):
            sprint.with_transition(SprintStatus.COMPLETED)

    @pytest.mark.unit
    def test_status_override_rejected(self) -> None:
        sprint = self._make_planning_sprint()
        with pytest.raises(ValueError, match="status override"):
            sprint.with_transition(
                SprintStatus.ACTIVE,
                status=SprintStatus.COMPLETED,
                start_date="2026-04-01",
            )

    @pytest.mark.unit
    def test_disallowed_override_rejected(self) -> None:
        sprint = self._make_planning_sprint()
        with pytest.raises(ValueError, match="does not allow overriding"):
            sprint.with_transition(
                SprintStatus.ACTIVE,
                start_date="2026-04-01",
                sprint_number=99,
            )

    @pytest.mark.unit
    def test_full_lifecycle_with_transition(self) -> None:
        sprint = self._make_planning_sprint()

        active = sprint.with_transition(
            SprintStatus.ACTIVE,
            start_date="2026-04-01",
        )
        in_review = active.with_transition(SprintStatus.IN_REVIEW)
        retro = in_review.with_transition(
            SprintStatus.RETROSPECTIVE,
        )
        completed = retro.with_transition(
            SprintStatus.COMPLETED,
            end_date="2026-04-14",
        )

        assert completed.status == SprintStatus.COMPLETED
        assert completed.start_date == "2026-04-01"
        assert completed.end_date == "2026-04-14"
