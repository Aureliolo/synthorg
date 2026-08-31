"""Tests for sprint velocity tracking."""

import pytest

from synthorg.engine.workflow.sprint_velocity import VelocityRecord

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
