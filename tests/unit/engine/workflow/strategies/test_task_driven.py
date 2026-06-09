"""Tests for the TaskDrivenStrategy reference implementation."""

import pytest

from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.engine.workflow.ceremony_context import CeremonyEvalContext
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyPolicyConfig,
    CeremonyStrategyType,
)
from synthorg.engine.workflow.ceremony_strategy import (
    CeremonySchedulingStrategy,
)
from synthorg.engine.workflow.sprint_config import (
    SprintCeremonyConfig,
    SprintConfig,
)
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.strategies.task_driven import (
    TaskDrivenStrategy,
)
from synthorg.engine.workflow.velocity_types import VelocityCalcType


def _make_ceremony(
    name: str = "standup",
    trigger: str = "every_n_completions",
    every_n: int = 5,
    sprint_percentage: float | None = None,
) -> SprintCeremonyConfig:
    """Create a ceremony config with task-driven policy override."""
    config: dict[str, object] = {"trigger": trigger}
    if trigger == "every_n_completions":
        config["every_n_completions"] = every_n
    if sprint_percentage is not None:
        config["sprint_percentage"] = sprint_percentage
    return SprintCeremonyConfig(
        name=name,
        protocol=MeetingProtocolType.ROUND_ROBIN,
        policy_override=CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.TASK_DRIVEN,
            strategy_config=config,
        ),
    )


def _make_sprint(
    task_count: int = 10,
    completed_count: int = 0,
    status: SprintStatus = SprintStatus.ACTIVE,
) -> Sprint:
    """Create a sprint with the given task/completion counts."""
    task_ids = tuple(f"task-{i}" for i in range(task_count))
    completed_ids = tuple(f"task-{i}" for i in range(completed_count))
    kwargs: dict[str, object] = {
        "id": "sprint-1",
        "name": "Sprint 1",
        "sprint_number": 1,
        "status": status,
        "task_ids": task_ids,
        "completed_task_ids": completed_ids,
        "story_points_committed": float(task_count * 3),
        "story_points_completed": float(completed_count * 3),
    }
    if status is not SprintStatus.PLANNING:
        kwargs["start_date"] = "2026-04-01T00:00:00"
    if status is SprintStatus.COMPLETED:
        kwargs["end_date"] = "2026-04-14T00:00:00"
    return Sprint.model_validate(kwargs)


def _make_context(
    completions_since_last: int = 0,
    total_completions: int = 0,
    total_tasks: int = 10,
    sprint_pct: float = 0.0,
) -> CeremonyEvalContext:
    """Create an evaluation context."""
    return CeremonyEvalContext(
        completions_since_last_trigger=completions_since_last,
        total_completions_this_sprint=total_completions,
        total_tasks_in_sprint=total_tasks,
        elapsed_seconds=60.0,
        budget_consumed_fraction=0.0,
        budget_remaining=0.0,
        velocity_history=(),
        external_events=(),
        sprint_percentage_complete=sprint_pct,
        story_points_completed=sprint_pct * total_tasks * 3,
        story_points_committed=float(total_tasks * 3),
    )


class TestTaskDrivenStrategyProtocol:
    """Verify TaskDrivenStrategy satisfies the protocol."""

    @pytest.mark.unit
    def test_is_protocol_instance(self) -> None:
        strategy = TaskDrivenStrategy()
        assert isinstance(strategy, CeremonySchedulingStrategy)

    @pytest.mark.unit
    def test_strategy_type(self) -> None:
        assert TaskDrivenStrategy().strategy_type is CeremonyStrategyType.TASK_DRIVEN

    @pytest.mark.unit
    def test_default_velocity_calculator(self) -> None:
        assert (
            TaskDrivenStrategy().get_default_velocity_calculator()
            is VelocityCalcType.TASK_DRIVEN
        )


class TestShouldFireCeremony:
    """should_fire_ceremony() tests."""

    @pytest.mark.unit
    def test_every_n_fires_at_threshold(self) -> None:
        strategy = TaskDrivenStrategy()
        ceremony = _make_ceremony(trigger="every_n_completions", every_n=5)
        ctx = _make_context(completions_since_last=5, total_tasks=20)
        assert strategy.should_fire_ceremony(ceremony, _make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_every_n_does_not_fire_below_threshold(self) -> None:
        strategy = TaskDrivenStrategy()
        ceremony = _make_ceremony(trigger="every_n_completions", every_n=5)
        ctx = _make_context(completions_since_last=4, total_tasks=20)
        assert strategy.should_fire_ceremony(ceremony, _make_sprint(), ctx) is False

    @pytest.mark.unit
    def test_every_n_fires_above_threshold(self) -> None:
        strategy = TaskDrivenStrategy()
        ceremony = _make_ceremony(trigger="every_n_completions", every_n=5)
        ctx = _make_context(completions_since_last=7, total_tasks=20)
        assert strategy.should_fire_ceremony(ceremony, _make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_sprint_end_fires_at_100_pct(self) -> None:
        strategy = TaskDrivenStrategy()
        ceremony = _make_ceremony(trigger="sprint_end")
        ctx = _make_context(sprint_pct=1.0, total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, _make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_sprint_end_does_not_fire_below_100(self) -> None:
        strategy = TaskDrivenStrategy()
        ceremony = _make_ceremony(trigger="sprint_end")
        ctx = _make_context(sprint_pct=0.9, total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, _make_sprint(), ctx) is False

    @pytest.mark.unit
    def test_sprint_midpoint_fires_at_50_pct(self) -> None:
        strategy = TaskDrivenStrategy()
        ceremony = _make_ceremony(trigger="sprint_midpoint")
        ctx = _make_context(sprint_pct=0.5, total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, _make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_sprint_midpoint_does_not_fire_below_50(self) -> None:
        strategy = TaskDrivenStrategy()
        ceremony = _make_ceremony(trigger="sprint_midpoint")
        ctx = _make_context(sprint_pct=0.4, total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, _make_sprint(), ctx) is False

    @pytest.mark.unit
    def test_sprint_start_always_false_in_per_task_eval(self) -> None:
        """sprint_start is handled as one-shot by scheduler, not per-task."""
        strategy = TaskDrivenStrategy()
        ceremony = _make_ceremony(trigger="sprint_start")
        ctx = _make_context(total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, _make_sprint(), ctx) is False

    @pytest.mark.unit
    def test_sprint_percentage_fires_at_threshold(self) -> None:
        strategy = TaskDrivenStrategy()
        ceremony = _make_ceremony(
            trigger="sprint_percentage",
            sprint_percentage=75.0,
        )
        ctx = _make_context(sprint_pct=0.75, total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, _make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_sprint_percentage_does_not_fire_below(self) -> None:
        strategy = TaskDrivenStrategy()
        ceremony = _make_ceremony(
            trigger="sprint_percentage",
            sprint_percentage=75.0,
        )
        ctx = _make_context(sprint_pct=0.7, total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, _make_sprint(), ctx) is False

    @pytest.mark.unit
    def test_no_policy_override_returns_false(self) -> None:
        strategy = TaskDrivenStrategy()
        ceremony = SprintCeremonyConfig(
            name="standup",
            protocol=MeetingProtocolType.ROUND_ROBIN,
            frequency=MeetingFrequency.DAILY,
        )
        ctx = _make_context(total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, _make_sprint(), ctx) is False

    @pytest.mark.unit
    def test_empty_sprint_returns_false_for_percentage(self) -> None:
        strategy = TaskDrivenStrategy()
        ceremony = _make_ceremony(
            trigger="sprint_percentage",
            sprint_percentage=50.0,
        )
        ctx = _make_context(sprint_pct=0.0, total_tasks=0)
        sprint = _make_sprint(task_count=0)
        result = strategy.should_fire_ceremony(ceremony, sprint, ctx)
        assert result is False


class TestShouldTransitionSprint:
    """should_transition_sprint() tests."""

    @pytest.mark.unit
    def test_transitions_at_threshold(self) -> None:
        strategy = TaskDrivenStrategy()
        sprint = _make_sprint(task_count=10, completed_count=10)
        config = SprintConfig()  # default threshold = 1.0
        ctx = _make_context(sprint_pct=1.0, total_tasks=10)
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is SprintStatus.IN_REVIEW

    @pytest.mark.unit
    def test_does_not_transition_below_threshold(self) -> None:
        strategy = TaskDrivenStrategy()
        sprint = _make_sprint(task_count=10, completed_count=8)
        config = SprintConfig()
        ctx = _make_context(sprint_pct=0.8, total_tasks=10)
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is None

    @pytest.mark.unit
    def test_transitions_at_custom_threshold(self) -> None:
        strategy = TaskDrivenStrategy()
        sprint = _make_sprint(task_count=10, completed_count=8)
        config = SprintConfig(
            ceremony_policy=CeremonyPolicyConfig(
                transition_threshold=0.8,
            ),
        )
        ctx = _make_context(sprint_pct=0.8, total_tasks=10)
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is SprintStatus.IN_REVIEW

    @pytest.mark.unit
    def test_does_not_transition_non_active(self) -> None:
        strategy = TaskDrivenStrategy()
        sprint = _make_sprint(status=SprintStatus.PLANNING, completed_count=0)
        config = SprintConfig()
        ctx = _make_context(sprint_pct=1.0, total_tasks=10)
        assert strategy.should_transition_sprint(sprint, config, ctx) is None

    @pytest.mark.unit
    def test_empty_sprint_does_not_transition(self) -> None:
        strategy = TaskDrivenStrategy()
        sprint = _make_sprint(task_count=0, completed_count=0)
        config = SprintConfig()
        ctx = _make_context(sprint_pct=0.0, total_tasks=0)
        assert strategy.should_transition_sprint(sprint, config, ctx) is None


class TestValidateStrategyConfig:
    """validate_strategy_config() tests."""

    @pytest.mark.unit
    def test_valid_config(self) -> None:
        strategy = TaskDrivenStrategy()
        strategy.validate_strategy_config(
            {
                "trigger": "every_n_completions",
                "every_n_completions": 5,
            }
        )

    @pytest.mark.unit
    def test_invalid_trigger(self) -> None:
        strategy = TaskDrivenStrategy()
        with pytest.raises(ValueError, match="Invalid trigger"):
            strategy.validate_strategy_config({"trigger": "unknown"})

    @pytest.mark.unit
    def test_invalid_every_n(self) -> None:
        strategy = TaskDrivenStrategy()
        with pytest.raises(ValueError, match="positive integer"):
            strategy.validate_strategy_config({"every_n_completions": 0})

    @pytest.mark.unit
    def test_invalid_sprint_percentage(self) -> None:
        strategy = TaskDrivenStrategy()
        with pytest.raises(ValueError, match="between"):
            strategy.validate_strategy_config({"sprint_percentage": 101})

    @pytest.mark.unit
    def test_empty_config_valid(self) -> None:
        strategy = TaskDrivenStrategy()
        strategy.validate_strategy_config({})
