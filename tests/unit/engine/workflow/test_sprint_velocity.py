"""Tests for sprint velocity tracking."""

import pytest

from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.sprint_velocity import (
    VelocityRecord,
    calculate_average_velocity,
    record_velocity,
)


def _completed_sprint(**overrides: object) -> Sprint:
    defaults: dict[str, object] = {
        "id": "sprint-1",
        "name": "Sprint 1",
        "sprint_number": 1,
        "status": SprintStatus.COMPLETED,
        "start_date": "2026-04-01",
        "end_date": "2026-04-14",
        "duration_days": 14,
        "task_ids": ("t-1", "t-2"),
        "completed_task_ids": ("t-1",),
        "story_points_committed": 13.0,
        "story_points_completed": 8.0,
    }
    defaults.update(overrides)
    return Sprint(**defaults)  # type: ignore[arg-type]


# ── VelocityRecord ─────────────────────────────────────────────


class TestVelocityRecord:
    """VelocityRecord computed fields and validation."""

    @pytest.mark.unit
    def test_completion_ratio_computed(self) -> None:
        record = VelocityRecord(
            sprint_id="sprint-1",
            sprint_number=1,
            story_points_committed=10.0,
            story_points_completed=7.0,
            duration_days=14,
        )
        assert record.completion_ratio == pytest.approx(0.7)

    @pytest.mark.unit
    def test_completion_ratio_zero_committed(self) -> None:
        record = VelocityRecord(
            sprint_id="sprint-1",
            sprint_number=1,
            story_points_committed=0.0,
            story_points_completed=0.0,
            duration_days=14,
        )
        assert record.completion_ratio == 0.0

    @pytest.mark.unit
    def test_completion_ratio_perfect(self) -> None:
        record = VelocityRecord(
            sprint_id="sprint-1",
            sprint_number=1,
            story_points_committed=10.0,
            story_points_completed=10.0,
            duration_days=14,
        )
        assert record.completion_ratio == pytest.approx(1.0)

    @pytest.mark.unit
    def test_new_optional_fields_default_none(self) -> None:
        record = VelocityRecord(
            sprint_id="sprint-1",
            sprint_number=1,
            story_points_committed=50.0,
            story_points_completed=42.0,
            duration_days=14,
        )
        assert record.task_completion_count is None
        assert record.wall_clock_seconds is None
        assert record.budget_consumed is None

    @pytest.mark.unit
    def test_new_optional_fields_with_values(self) -> None:
        record = VelocityRecord(
            sprint_id="sprint-1",
            sprint_number=1,
            story_points_committed=50.0,
            story_points_completed=42.0,
            duration_days=14,
            task_completion_count=15,
            wall_clock_seconds=3600.0,
            budget_consumed=25.50,
        )
        assert record.task_completion_count == 15
        assert record.wall_clock_seconds == 3600.0
        assert record.budget_consumed == 25.50

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("task_completion_count", -1),
            ("wall_clock_seconds", -1.0),
            ("budget_consumed", -0.01),
        ],
    )
    def test_negative_optional_fields_rejected(
        self,
        field: str,
        value: int | float,
    ) -> None:
        kwargs: dict[str, object] = {
            "sprint_id": "sprint-1",
            "sprint_number": 1,
            "story_points_committed": 50.0,
            "story_points_completed": 42.0,
            "duration_days": 14,
            field: value,
        }
        with pytest.raises(ValueError, match="greater than or equal"):
            VelocityRecord.model_validate(kwargs)


# ── record_velocity ────────────────────────────────────────────


class TestRecordVelocity:
    """record_velocity creates records from completed sprints."""

    @pytest.mark.unit
    def test_record_from_completed_sprint(self) -> None:
        sprint = _completed_sprint()
        record = record_velocity(sprint)
        assert record.sprint_id == "sprint-1"
        assert record.sprint_number == 1
        assert record.story_points_committed == 13.0
        assert record.story_points_completed == 8.0
        assert record.duration_days == 14

    @pytest.mark.unit
    def test_record_with_new_optional_fields(self) -> None:
        sprint = _completed_sprint()
        record = record_velocity(
            sprint,
            task_completion_count=5,
            wall_clock_seconds=1800.0,
            budget_consumed=12.50,
        )
        assert record.task_completion_count == 5
        assert record.wall_clock_seconds == 1800.0
        assert record.budget_consumed == 12.50

    @pytest.mark.unit
    def test_reject_non_completed_sprint(self) -> None:
        sprint = Sprint(
            id="sprint-1",
            name="Sprint 1",
            sprint_number=1,
            status=SprintStatus.ACTIVE,
            start_date="2026-04-01",
        )
        with pytest.raises(ValueError, match="must be 'completed'"):
            record_velocity(sprint)


# ── calculate_average_velocity ─────────────────────────────────


class TestCalculateAverageVelocity:
    """calculate_average_velocity computes rolling averages."""

    def _make_records(self, points: list[float]) -> list[VelocityRecord]:
        return [
            VelocityRecord(
                sprint_id=f"sprint-{i + 1}",
                sprint_number=i + 1,
                story_points_committed=p,
                story_points_completed=p,
                duration_days=14,
            )
            for i, p in enumerate(points)
        ]

    @pytest.mark.unit
    def test_empty_records(self) -> None:
        assert calculate_average_velocity([]) == 0.0

    @pytest.mark.unit
    def test_single_record(self) -> None:
        records = self._make_records([10.0])
        assert calculate_average_velocity(records) == pytest.approx(10.0)

    @pytest.mark.unit
    def test_window_equals_records(self) -> None:
        records = self._make_records([10.0, 20.0, 30.0])
        avg = calculate_average_velocity(records, window=3)
        assert avg == pytest.approx(20.0)

    @pytest.mark.unit
    def test_window_smaller_than_records(self) -> None:
        records = self._make_records([5.0, 10.0, 15.0, 20.0, 25.0])
        avg = calculate_average_velocity(records, window=3)
        assert avg == pytest.approx(20.0)

    @pytest.mark.unit
    def test_window_larger_than_records(self) -> None:
        records = self._make_records([10.0, 20.0])
        avg = calculate_average_velocity(records, window=5)
        assert avg == pytest.approx(15.0)

    @pytest.mark.unit
    def test_window_of_one(self) -> None:
        records = self._make_records([5.0, 10.0, 15.0])
        avg = calculate_average_velocity(records, window=1)
        assert avg == pytest.approx(15.0)

    @pytest.mark.unit
    def test_invalid_window_raises(self) -> None:
        with pytest.raises(ValueError, match="window must be >= 1"):
            calculate_average_velocity([], window=0)
