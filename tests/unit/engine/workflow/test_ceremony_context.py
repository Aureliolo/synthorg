"""Tests for CeremonyEvalContext dataclass."""

import pytest

from synthorg.engine.workflow.ceremony_context import CeremonyEvalContext


class TestCeremonyEvalContext:
    """CeremonyEvalContext tests."""

    @pytest.mark.unit
    def test_construction(self) -> None:
        ctx = CeremonyEvalContext(
            completions_since_last_trigger=3,
            total_completions_this_sprint=10,
            total_tasks_in_sprint=20,
            elapsed_seconds=120.0,
            budget_consumed_fraction=0.5,
            budget_remaining=100.0,
            velocity_history=(),
            external_events=(),
            sprint_percentage_complete=0.5,
            story_points_completed=21.0,
            story_points_committed=42.0,
        )
        assert ctx.completions_since_last_trigger == 3
        assert ctx.total_completions_this_sprint == 10
        assert ctx.total_tasks_in_sprint == 20
        assert ctx.elapsed_seconds == 120.0
        assert ctx.budget_consumed_fraction == 0.5
        assert ctx.budget_remaining == 100.0
        assert ctx.velocity_history == ()
        assert ctx.external_events == ()
        assert ctx.sprint_percentage_complete == 0.5
        assert ctx.story_points_completed == 21.0
        assert ctx.story_points_committed == 42.0

    @pytest.mark.unit
    def test_frozen(self) -> None:
        ctx = CeremonyEvalContext(
            completions_since_last_trigger=0,
            total_completions_this_sprint=0,
            total_tasks_in_sprint=10,
            elapsed_seconds=0.0,
            budget_consumed_fraction=0.0,
            budget_remaining=200.0,
            velocity_history=(),
            external_events=(),
            sprint_percentage_complete=0.0,
            story_points_completed=0.0,
            story_points_committed=50.0,
        )
        with pytest.raises(AttributeError):
            ctx.completions_since_last_trigger = 5  # type: ignore[misc]

    @pytest.mark.unit
    def test_with_velocity_history(self) -> None:
        from synthorg.engine.workflow.sprint_velocity import VelocityRecord

        record = VelocityRecord(
            sprint_id="sprint-1",
            sprint_number=1,
            story_points_committed=50.0,
            story_points_completed=42.0,
            duration_days=14,
        )
        ctx = CeremonyEvalContext(
            completions_since_last_trigger=0,
            total_completions_this_sprint=0,
            total_tasks_in_sprint=10,
            elapsed_seconds=0.0,
            budget_consumed_fraction=0.0,
            budget_remaining=0.0,
            velocity_history=(record,),
            external_events=(),
            sprint_percentage_complete=0.0,
            story_points_completed=0.0,
            story_points_committed=50.0,
        )
        assert len(ctx.velocity_history) == 1
        assert ctx.velocity_history[0].sprint_id == "sprint-1"

    @pytest.mark.unit
    def test_with_external_events(self) -> None:
        ctx = CeremonyEvalContext(
            completions_since_last_trigger=0,
            total_completions_this_sprint=0,
            total_tasks_in_sprint=10,
            elapsed_seconds=0.0,
            budget_consumed_fraction=0.0,
            budget_remaining=0.0,
            velocity_history=(),
            external_events=("pr_merged", "deploy_complete"),
            sprint_percentage_complete=0.0,
            story_points_completed=0.0,
            story_points_committed=50.0,
        )
        assert ctx.external_events == ("pr_merged", "deploy_complete")

    @pytest.mark.unit
    def test_equality(self) -> None:
        kwargs: dict[str, object] = {
            "completions_since_last_trigger": 1,
            "total_completions_this_sprint": 5,
            "total_tasks_in_sprint": 10,
            "elapsed_seconds": 60.0,
            "budget_consumed_fraction": 0.0,
            "budget_remaining": 0.0,
            "velocity_history": (),
            "external_events": (),
            "sprint_percentage_complete": 0.5,
            "story_points_completed": 25.0,
            "story_points_committed": 50.0,
        }
        a = CeremonyEvalContext(**kwargs)  # type: ignore[arg-type]
        b = CeremonyEvalContext(**kwargs)  # type: ignore[arg-type]
        assert a == b


_VALID_KWARGS: dict[str, object] = {
    "completions_since_last_trigger": 0,
    "total_completions_this_sprint": 0,
    "total_tasks_in_sprint": 10,
    "elapsed_seconds": 0.0,
    "budget_consumed_fraction": 0.0,
    "budget_remaining": 0.0,
    "velocity_history": (),
    "external_events": (),
    "sprint_percentage_complete": 0.0,
    "story_points_completed": 0.0,
    "story_points_committed": 50.0,
}


class TestCeremonyEvalContextValidation:
    """Validation in __post_init__."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("completions_since_last_trigger", -1),
            ("total_completions_this_sprint", -1),
            ("total_tasks_in_sprint", -1),
            ("elapsed_seconds", -0.1),
            ("budget_remaining", -0.01),
            ("story_points_completed", -1.0),
            ("story_points_committed", -1.0),
        ],
    )
    def test_negative_values_rejected(
        self,
        field: str,
        value: int | float,
    ) -> None:
        kwargs = {**_VALID_KWARGS, field: value}
        with pytest.raises(ValueError, match="must be"):
            CeremonyEvalContext(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("budget_consumed_fraction", -0.01),
            ("budget_consumed_fraction", 1.01),
            ("sprint_percentage_complete", -0.01),
            ("sprint_percentage_complete", 1.01),
        ],
    )
    def test_fraction_out_of_range_rejected(
        self,
        field: str,
        value: float,
    ) -> None:
        kwargs = {**_VALID_KWARGS, field: value}
        with pytest.raises(ValueError, match="must be"):
            CeremonyEvalContext(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "field",
        [
            "elapsed_seconds",
            "budget_remaining",
            "budget_consumed_fraction",
            "sprint_percentage_complete",
            "story_points_completed",
            "story_points_committed",
        ],
    )
    def test_nan_rejected(self, field: str) -> None:
        kwargs = {**_VALID_KWARGS, field: float("nan")}
        with pytest.raises(ValueError, match="finite"):
            CeremonyEvalContext(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "field",
        [
            "elapsed_seconds",
            "budget_remaining",
            "story_points_completed",
            "story_points_committed",
        ],
    )
    def test_inf_rejected(self, field: str) -> None:
        kwargs = {**_VALID_KWARGS, field: float("inf")}
        with pytest.raises(ValueError, match="finite"):
            CeremonyEvalContext(**kwargs)  # type: ignore[arg-type]
