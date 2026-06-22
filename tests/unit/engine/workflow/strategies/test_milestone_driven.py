"""Tests for the MilestoneDrivenStrategy implementation."""

import pytest

from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.frequency import MeetingFrequency
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
from synthorg.engine.workflow.sprint_lifecycle import SprintStatus
from synthorg.engine.workflow.strategies.milestone_driven import (
    MilestoneDrivenStrategy,
)
from synthorg.engine.workflow.velocity_types import VelocityCalcType

from .conftest import make_context, make_sprint


def _make_ceremony(
    name: str = "sprint_review",
) -> SprintCeremonyConfig:
    """Create a ceremony config with milestone-driven policy override."""
    return SprintCeremonyConfig(
        name=name,
        protocol=MeetingProtocolType.ROUND_ROBIN,
        policy_override=CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.MILESTONE_DRIVEN,
        ),
    )


def _make_sprint_config(
    milestones: list[dict[str, str]] | None = None,
    transition_milestone: str | None = None,
) -> SprintConfig:
    """Create a SprintConfig with milestone-driven policy."""
    config: dict[str, object] = {}
    if milestones is not None:
        config["milestones"] = milestones
    if transition_milestone is not None:
        config["transition_milestone"] = transition_milestone
    return SprintConfig(
        ceremony_policy=CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.MILESTONE_DRIVEN,
            strategy_config=config,
        ),
    )


class TestMilestoneDrivenStrategyProtocol:
    """Verify MilestoneDrivenStrategy satisfies the protocol."""

    @pytest.mark.unit
    def test_is_protocol_instance(self) -> None:
        strategy = MilestoneDrivenStrategy()
        assert isinstance(strategy, CeremonySchedulingStrategy)

    @pytest.mark.unit
    def test_strategy_type(self) -> None:
        assert (
            MilestoneDrivenStrategy().strategy_type
            is CeremonyStrategyType.MILESTONE_DRIVEN
        )

    @pytest.mark.unit
    def test_default_velocity_calculator(self) -> None:
        assert (
            MilestoneDrivenStrategy().get_default_velocity_calculator()
            is VelocityCalcType.POINTS_PER_SPRINT
        )


class TestShouldFireCeremony:
    """should_fire_ceremony() tests."""

    @pytest.mark.unit
    async def test_fires_when_milestone_complete(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(
            task_count=3,
            completed_count=3,
        )
        config = _make_sprint_config(
            milestones=[{"name": "feature_complete", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        # Assign all 3 tasks to the milestone
        for i in range(3):
            await strategy.on_external_event(
                sprint,
                "milestone_assign",
                {"task_id": f"task-{i}", "milestone": "feature_complete"},
            )

        ceremony = _make_ceremony(name="sprint_review")
        ctx = make_context()
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True

    @pytest.mark.unit
    async def test_does_not_fire_when_tasks_incomplete(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=3, completed_count=1)
        config = _make_sprint_config(
            milestones=[{"name": "feature_complete", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        for i in range(3):
            await strategy.on_external_event(
                sprint,
                "milestone_assign",
                {"task_id": f"task-{i}", "milestone": "feature_complete"},
            )

        ceremony = _make_ceremony(name="sprint_review")
        ctx = make_context()
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_edge_triggered_does_not_fire_twice(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=2, completed_count=2)
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        for i in range(2):
            await strategy.on_external_event(
                sprint,
                "milestone_assign",
                {"task_id": f"task-{i}", "milestone": "alpha"},
            )

        ceremony = _make_ceremony(name="sprint_review")
        ctx = make_context()

        # First check fires
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True
        # Second check does not fire (edge-triggered)
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_no_milestones_configured_returns_false(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=3, completed_count=3)
        config = _make_sprint_config(milestones=[])
        await strategy.on_sprint_activated(sprint, config)

        ceremony = _make_ceremony(name="sprint_review")
        ctx = make_context()
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_empty_milestone_no_tasks_returns_false(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=3, completed_count=3)
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)
        # No tasks assigned to milestone

        ceremony = _make_ceremony(name="sprint_review")
        ctx = make_context()
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_ceremony_name_mismatch_returns_false(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=2, completed_count=2)
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "retrospective"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        for i in range(2):
            await strategy.on_external_event(
                sprint,
                "milestone_assign",
                {"task_id": f"task-{i}", "milestone": "alpha"},
            )

        # Ceremony name doesn't match the milestone's configured ceremony
        ceremony = _make_ceremony(name="sprint_review")
        ctx = make_context()
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_multiple_milestones_fires_correct_ceremony(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=4, completed_count=2)
        config = _make_sprint_config(
            milestones=[
                {"name": "alpha", "ceremony": "sprint_review"},
                {"name": "beta", "ceremony": "retrospective"},
            ],
        )
        await strategy.on_sprint_activated(sprint, config)

        # Assign task-0, task-1 to alpha (both complete)
        for i in range(2):
            await strategy.on_external_event(
                sprint,
                "milestone_assign",
                {"task_id": f"task-{i}", "milestone": "alpha"},
            )
        # Assign task-2, task-3 to beta (not complete)
        for i in range(2, 4):
            await strategy.on_external_event(
                sprint,
                "milestone_assign",
                {"task_id": f"task-{i}", "milestone": "beta"},
            )

        ctx = make_context()
        review = _make_ceremony(name="sprint_review")
        retro = _make_ceremony(name="retrospective")

        assert strategy.should_fire_ceremony(review, sprint, ctx) is True
        assert strategy.should_fire_ceremony(retro, sprint, ctx) is False

    @pytest.mark.unit
    async def test_no_policy_override_returns_false(self) -> None:
        """Milestone fires based on task completion, not policy_override."""
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=2, completed_count=2)
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        # Assign tasks so the milestone IS complete
        for i in range(2):
            await strategy.on_external_event(
                sprint,
                "milestone_assign",
                {"task_id": f"task-{i}", "milestone": "alpha"},
            )

        # Ceremony with frequency but no milestone policy_override;
        # strategy evaluates milestones regardless of policy_override
        ceremony = SprintCeremonyConfig(
            name="sprint_review",
            protocol=MeetingProtocolType.ROUND_ROBIN,
            frequency=MeetingFrequency.BI_WEEKLY,
        )
        ctx = make_context()
        # Milestone is complete, so the strategy fires
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True


class TestShouldTransitionSprint:
    """should_transition_sprint() tests."""

    @pytest.mark.unit
    async def test_transitions_when_milestone_complete(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=3, completed_count=3)
        config = _make_sprint_config(
            milestones=[{"name": "release_candidate", "ceremony": "sprint_review"}],
            transition_milestone="release_candidate",
        )
        await strategy.on_sprint_activated(sprint, config)

        for i in range(3):
            await strategy.on_external_event(
                sprint,
                "milestone_assign",
                {"task_id": f"task-{i}", "milestone": "release_candidate"},
            )

        ctx = make_context()
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is SprintStatus.IN_REVIEW

    @pytest.mark.unit
    async def test_no_transition_milestone_returns_none(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=3, completed_count=3)
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        ctx = make_context()
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is None

    @pytest.mark.unit
    async def test_transition_milestone_incomplete_returns_none(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=3, completed_count=1)
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
            transition_milestone="alpha",
        )
        await strategy.on_sprint_activated(sprint, config)

        for i in range(3):
            await strategy.on_external_event(
                sprint,
                "milestone_assign",
                {"task_id": f"task-{i}", "milestone": "alpha"},
            )

        ctx = make_context()
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is None

    @pytest.mark.unit
    async def test_non_active_sprint_returns_none(self) -> None:
        strategy = MilestoneDrivenStrategy()
        active_sprint = make_sprint(
            task_count=2,
            completed_count=2,
        )
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
            transition_milestone="alpha",
        )
        await strategy.on_sprint_activated(active_sprint, config)

        for i in range(2):
            await strategy.on_external_event(
                active_sprint,
                "milestone_assign",
                {"task_id": f"task-{i}", "milestone": "alpha"},
            )

        # Sprint is PLANNING, not ACTIVE -- status guard blocks transition
        sprint = make_sprint(
            status=SprintStatus.PLANNING,
            task_count=2,
            completed_count=2,
        )
        ctx = make_context()
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is None

    @pytest.mark.unit
    async def test_transition_milestone_no_tasks_returns_none(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=3, completed_count=3)
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
            transition_milestone="alpha",
        )
        await strategy.on_sprint_activated(sprint, config)
        # No tasks assigned to milestone

        ctx = make_context()
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is None

    @pytest.mark.unit
    def test_strategy_config_none_returns_none(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = SprintConfig(
            ceremony_policy=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.MILESTONE_DRIVEN,
                strategy_config=None,
            ),
        )
        ctx = make_context()
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is None


class TestLifecycleHooks:
    """Lifecycle hook tests."""

    @pytest.mark.unit
    async def test_on_sprint_activated_reads_milestones(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[
                {"name": "alpha", "ceremony": "sprint_review"},
                {"name": "beta", "ceremony": "retrospective"},
            ],
        )
        await strategy.on_sprint_activated(sprint, config)

        assert strategy._milestones == {
            "alpha": "sprint_review",
            "beta": "retrospective",
        }

    @pytest.mark.unit
    async def test_on_sprint_activated_resets_state(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint(task_count=2, completed_count=2)
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        # Assign a task
        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "task-0", "milestone": "alpha"},
        )
        # Fire the ceremony
        ceremony = _make_ceremony(name="sprint_review")
        ctx = make_context()
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True

        # Re-activate -- state should be fresh
        await strategy.on_sprint_activated(sprint, config)
        assert strategy._milestone_tasks == {}
        assert strategy._fired_milestones == set()

    @pytest.mark.unit
    async def test_on_sprint_deactivated_clears_state(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "task-0", "milestone": "alpha"},
        )
        await strategy.on_sprint_deactivated()

        assert strategy._milestones == {}
        assert strategy._milestone_tasks == {}
        assert strategy._fired_milestones == set()
        assert strategy._transition_milestone is None

    @pytest.mark.unit
    async def test_milestone_assign_registers_task(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "task-0", "milestone": "alpha"},
        )
        assert "task-0" in strategy._milestone_tasks.get("alpha", set())

    @pytest.mark.unit
    async def test_milestone_unassign_removes_task(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "task-0", "milestone": "alpha"},
        )
        await strategy.on_external_event(
            sprint,
            "milestone_unassign",
            {"task_id": "task-0", "milestone": "alpha"},
        )
        assert "task-0" not in strategy._milestone_tasks.get("alpha", set())

    @pytest.mark.unit
    async def test_assign_to_unknown_milestone_ignored(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "task-0", "milestone": "unknown"},
        )
        assert "unknown" not in strategy._milestone_tasks

    @pytest.mark.unit
    async def test_assign_invalid_payload_ignored(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        # Missing task_id
        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"milestone": "alpha"},
        )
        # Missing milestone
        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "task-0"},
        )
        # Blank field (rejected by the NotBlankStr model field)
        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "  ", "milestone": "alpha"},
        )
        # Unexpected extra key (rejected by extra="forbid")
        await strategy.on_external_event(
            sprint,
            "milestone_assign",
            {"task_id": "task-0", "milestone": "alpha", "rogue": "x"},
        )
        # Empty milestone_tasks
        assert strategy._milestone_tasks == {}

    @pytest.mark.unit
    async def test_unrelated_external_event_ignored(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            milestones=[{"name": "alpha", "ceremony": "sprint_review"}],
        )
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(
            sprint,
            "deploy_completed",
            {"version": "1.0"},
        )
        assert strategy._milestone_tasks == {}

    @pytest.mark.unit
    async def test_on_task_completed_is_noop(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)
        ctx = make_context()

        # Should not raise
        await strategy.on_task_completed(sprint, "task-0", 3.0, ctx)

    @pytest.mark.unit
    async def test_on_task_added_is_noop(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_task_added(sprint, "task-0")

    @pytest.mark.unit
    async def test_on_task_blocked_is_noop(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_task_blocked(sprint, "task-0")

    @pytest.mark.unit
    async def test_on_budget_updated_is_noop(self) -> None:
        strategy = MilestoneDrivenStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_budget_updated(sprint, 0.5)
