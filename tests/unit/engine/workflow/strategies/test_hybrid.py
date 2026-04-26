"""Tests for the HybridStrategy (first-wins) implementation."""

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
from synthorg.engine.workflow.strategies.hybrid import (
    HybridStrategy,
)
from synthorg.engine.workflow.velocity_types import VelocityCalcType

from .conftest import SECONDS_PER_DAY, make_context, make_sprint

# -- Helpers -----------------------------------------------------------------


def _make_ceremony(
    name: str = "standup",
    frequency: MeetingFrequency | None = MeetingFrequency.DAILY,
    trigger: str | None = None,
    every_n: int = 5,
    sprint_percentage: float | None = None,
) -> SprintCeremonyConfig:
    """Create a ceremony config for hybrid strategy tests.

    When *trigger* is None and *frequency* is set, the ceremony has
    only a calendar leg.  When *trigger* is set, the ceremony has a
    task-driven leg (via policy_override).
    """
    strategy_config: dict[str, object] | None = None
    if trigger is not None:
        strategy_config = {"trigger": trigger}
        if trigger == "every_n_completions":
            strategy_config["every_n_completions"] = every_n
        if sprint_percentage is not None:
            strategy_config["sprint_percentage"] = sprint_percentage
    override: CeremonyPolicyConfig | None = None
    if strategy_config is not None:
        override = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.HYBRID,
            strategy_config=strategy_config,
        )
    return SprintCeremonyConfig(
        name=name,
        protocol=MeetingProtocolType.ROUND_ROBIN,
        frequency=frequency,
        policy_override=override,
    )


# -- Protocol conformance ---------------------------------------------------


class TestHybridStrategyProtocol:
    """Verify HybridStrategy satisfies the protocol."""

    @pytest.mark.unit
    def test_is_protocol_instance(self) -> None:
        strategy = HybridStrategy()
        assert isinstance(strategy, CeremonySchedulingStrategy)

    @pytest.mark.unit
    def test_strategy_type(self) -> None:
        assert HybridStrategy().strategy_type is CeremonyStrategyType.HYBRID

    @pytest.mark.unit
    def test_default_velocity_calculator(self) -> None:
        assert (
            HybridStrategy().get_default_velocity_calculator()
            is VelocityCalcType.MULTI_DIMENSIONAL
        )


# -- should_fire_ceremony ---------------------------------------------------


class TestShouldFireCeremony:
    """should_fire_ceremony() tests."""

    @pytest.mark.unit
    def test_calendar_leg_fires_when_interval_elapsed(self) -> None:
        """Frequency-only ceremony fires on time interval."""
        strategy = HybridStrategy()
        ceremony = _make_ceremony(frequency=MeetingFrequency.DAILY, trigger=None)
        ctx = make_context(elapsed_seconds=SECONDS_PER_DAY)
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_calendar_leg_does_not_fire_before_interval(self) -> None:
        strategy = HybridStrategy()
        ceremony = _make_ceremony(frequency=MeetingFrequency.DAILY, trigger=None)
        ctx = make_context(elapsed_seconds=SECONDS_PER_DAY - 1.0)
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is False

    @pytest.mark.unit
    def test_task_leg_fires_when_every_n_reached(self) -> None:
        """Task-only ceremony (no frequency) fires on count threshold."""
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=None,
            trigger="every_n_completions",
            every_n=5,
        )
        ctx = make_context(completions_since_last=5, total_tasks=20)
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_task_leg_does_not_fire_below_threshold(self) -> None:
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=None,
            trigger="every_n_completions",
            every_n=5,
        )
        ctx = make_context(completions_since_last=4, total_tasks=20)
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is False

    @pytest.mark.unit
    def test_task_leg_sprint_percentage(self) -> None:
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=None,
            trigger="sprint_percentage",
            sprint_percentage=75.0,
        )
        ctx = make_context(sprint_pct=0.75, total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_calendar_fires_first(self) -> None:
        """Calendar interval met, task threshold not -- fires."""
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=MeetingFrequency.DAILY,
            trigger="every_n_completions",
            every_n=10,
        )
        # Time elapsed, but only 3 completions (below 10).
        ctx = make_context(
            elapsed_seconds=SECONDS_PER_DAY,
            completions_since_last=3,
        )
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_task_fires_first(self) -> None:
        """Task threshold met before calendar interval -- fires."""
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=MeetingFrequency.DAILY,
            trigger="every_n_completions",
            every_n=5,
        )
        # Only half a day elapsed, but 5 completions.
        ctx = make_context(
            elapsed_seconds=SECONDS_PER_DAY / 2,
            completions_since_last=5,
        )
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_task_fire_resets_calendar_timer(self) -> None:
        """When task-driven fires first, calendar timer resets."""
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=MeetingFrequency.DAILY,
            trigger="every_n_completions",
            every_n=5,
        )
        sprint = make_sprint()

        # Task fires at T=50000 (before daily interval of 86400).
        ctx1 = make_context(
            elapsed_seconds=50_000.0,
            completions_since_last=5,
        )
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx1) is True

        # Calendar would have fired at T=86400, but timer was reset to 50000.
        # Next calendar fire should be at 50000 + 86400 = 136400.
        ctx2 = make_context(
            elapsed_seconds=SECONDS_PER_DAY,
            completions_since_last=2,
        )
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx2) is False

        # Fires at 136400.
        ctx3 = make_context(
            elapsed_seconds=136_400.0,
            completions_since_last=2,
        )
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx3) is True

    @pytest.mark.unit
    def test_both_met_simultaneously_fires_once(self) -> None:
        """Both legs met at the same time -- fires once, returns True."""
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=MeetingFrequency.DAILY,
            trigger="every_n_completions",
            every_n=5,
        )
        ctx = make_context(
            elapsed_seconds=SECONDS_PER_DAY,
            completions_since_last=5,
        )
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_neither_fires(self) -> None:
        """Neither leg meets threshold."""
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=MeetingFrequency.DAILY,
            trigger="every_n_completions",
            every_n=10,
        )
        ctx = make_context(
            elapsed_seconds=SECONDS_PER_DAY / 2,
            completions_since_last=3,
        )
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is False

    @pytest.mark.unit
    def test_no_frequency_no_trigger_returns_false(self) -> None:
        """Ceremony with no frequency and no trigger."""
        strategy = HybridStrategy()
        ceremony = SprintCeremonyConfig(
            name="standup",
            protocol=MeetingProtocolType.ROUND_ROBIN,
            policy_override=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.HYBRID,
                strategy_config={},
            ),
        )
        ctx = make_context(
            elapsed_seconds=SECONDS_PER_DAY * 100,
            completions_since_last=100,
        )
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is False

    # Trigger branch coverage (Item 9)

    @pytest.mark.unit
    def test_sprint_start_trigger_never_fires(self) -> None:
        """sprint_start triggers fire via lifecycle hooks, not evaluation."""
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=None,
            trigger="sprint_start",
        )
        ctx = make_context(
            elapsed_seconds=0.0,
            total_tasks=10,
            sprint_pct=0.0,
        )
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is False

    @pytest.mark.unit
    def test_sprint_end_trigger_fires_at_100pct(self) -> None:
        """sprint_end fires when all tasks complete."""
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=None,
            trigger="sprint_end",
        )
        ctx = make_context(sprint_pct=1.0, total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_sprint_end_trigger_does_not_fire_below_100pct(self) -> None:
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=None,
            trigger="sprint_end",
        )
        ctx = make_context(sprint_pct=0.99, total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is False

    @pytest.mark.unit
    def test_sprint_midpoint_trigger_fires_at_50pct(self) -> None:
        """sprint_midpoint fires at 50% completion."""
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=None,
            trigger="sprint_midpoint",
        )
        ctx = make_context(sprint_pct=0.5, total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is True

    @pytest.mark.unit
    def test_sprint_midpoint_trigger_does_not_fire_below_50pct(self) -> None:
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=None,
            trigger="sprint_midpoint",
        )
        ctx = make_context(sprint_pct=0.49, total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is False

    @pytest.mark.unit
    def test_unrecognized_trigger_returns_false(self) -> None:
        """An unrecognized trigger string returns False with a warning."""
        strategy = HybridStrategy()
        ceremony = SprintCeremonyConfig(
            name="standup",
            protocol=MeetingProtocolType.ROUND_ROBIN,
            policy_override=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.HYBRID,
                strategy_config={"trigger": "nonexistent_trigger"},
            ),
        )
        ctx = make_context(sprint_pct=1.0, total_tasks=10)
        assert strategy.should_fire_ceremony(ceremony, make_sprint(), ctx) is False

    # Infinite firing prevention

    @pytest.mark.unit
    def test_percentage_trigger_does_not_refire_within_interval(self) -> None:
        """Once a percentage trigger fires, it is suppressed until next interval."""
        strategy = HybridStrategy()
        ceremony = _make_ceremony(
            frequency=MeetingFrequency.DAILY,
            trigger="sprint_percentage",
            sprint_percentage=50.0,
        )
        sprint = make_sprint()

        # First evaluation at day 1: percentage >= 50% AND interval elapsed.
        ctx1 = make_context(
            elapsed_seconds=SECONDS_PER_DAY,
            sprint_pct=0.5,
            total_tasks=10,
        )
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx1) is True

        # Same percentage shortly after -- should NOT re-fire.
        ctx2 = make_context(
            elapsed_seconds=SECONDS_PER_DAY + 100.0,
            sprint_pct=0.6,
            total_tasks=10,
        )
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx2) is False

        # After next interval elapses, should fire again.
        ctx3 = make_context(
            elapsed_seconds=2 * SECONDS_PER_DAY + 1.0,
            sprint_pct=0.7,
            total_tasks=10,
        )
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx3) is True


# -- should_transition_sprint ------------------------------------------------


class TestShouldTransitionSprint:
    """should_transition_sprint() tests."""

    @pytest.mark.unit
    def test_transitions_on_calendar_duration(self) -> None:
        """Calendar leg: elapsed >= duration_days * 86400."""
        strategy = HybridStrategy()
        sprint = make_sprint(duration_days=14)
        config = SprintConfig(duration_days=14)
        ctx = make_context(
            elapsed_seconds=14.0 * SECONDS_PER_DAY,
            sprint_pct=0.5,
        )
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is SprintStatus.IN_REVIEW

    @pytest.mark.unit
    def test_transitions_on_task_completion(self) -> None:
        """Task leg: sprint_percentage_complete >= threshold."""
        strategy = HybridStrategy()
        sprint = make_sprint(task_count=10, completed_count=10)
        config = SprintConfig(duration_days=14)
        # Only 1 day elapsed, but all tasks done.
        ctx = make_context(
            elapsed_seconds=SECONDS_PER_DAY,
            sprint_pct=1.0,
            total_tasks=10,
        )
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is SprintStatus.IN_REVIEW

    @pytest.mark.unit
    def test_calendar_met_task_not(self) -> None:
        """Calendar duration met, task completion below threshold -- transitions."""
        strategy = HybridStrategy()
        sprint = make_sprint(task_count=10, completed_count=5)
        config = SprintConfig(duration_days=14)
        ctx = make_context(
            elapsed_seconds=14.0 * SECONDS_PER_DAY,
            sprint_pct=0.5,
            total_tasks=10,
        )
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is SprintStatus.IN_REVIEW

    @pytest.mark.unit
    def test_task_met_calendar_not(self) -> None:
        """Task threshold met, calendar not -- transitions."""
        strategy = HybridStrategy()
        sprint = make_sprint(task_count=10, completed_count=10)
        config = SprintConfig(duration_days=14)
        ctx = make_context(
            elapsed_seconds=SECONDS_PER_DAY,
            sprint_pct=1.0,
            total_tasks=10,
        )
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is SprintStatus.IN_REVIEW

    @pytest.mark.unit
    def test_neither_transitions(self) -> None:
        strategy = HybridStrategy()
        sprint = make_sprint(task_count=10, completed_count=5)
        config = SprintConfig(duration_days=14)
        ctx = make_context(
            elapsed_seconds=SECONDS_PER_DAY,
            sprint_pct=0.5,
            total_tasks=10,
        )
        assert strategy.should_transition_sprint(sprint, config, ctx) is None

    @pytest.mark.unit
    def test_does_not_transition_non_active(self) -> None:
        strategy = HybridStrategy()
        sprint = make_sprint(status=SprintStatus.PLANNING, completed_count=0)
        config = SprintConfig()
        ctx = make_context(elapsed_seconds=100 * SECONDS_PER_DAY, sprint_pct=1.0)
        assert strategy.should_transition_sprint(sprint, config, ctx) is None

    @pytest.mark.unit
    def test_empty_sprint_calendar_only_transitions(self) -> None:
        """No tasks -- only calendar leg can transition."""
        strategy = HybridStrategy()
        sprint = make_sprint(task_count=0, completed_count=0)
        config = SprintConfig(duration_days=7)
        ctx = make_context(
            elapsed_seconds=7.0 * SECONDS_PER_DAY,
            sprint_pct=0.0,
            total_tasks=0,
        )
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is SprintStatus.IN_REVIEW

    @pytest.mark.unit
    def test_duration_from_strategy_config(self) -> None:
        """strategy_config.duration_days overrides config.duration_days."""
        strategy = HybridStrategy()
        sprint = make_sprint(duration_days=14)
        config = SprintConfig(
            duration_days=14,
            ceremony_policy=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.HYBRID,
                strategy_config={"duration_days": 7},
            ),
        )
        ctx = make_context(elapsed_seconds=7.0 * SECONDS_PER_DAY, sprint_pct=0.0)
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is SprintStatus.IN_REVIEW

    @pytest.mark.unit
    def test_custom_task_threshold(self) -> None:
        """Custom transition_threshold for task leg."""
        strategy = HybridStrategy()
        sprint = make_sprint(task_count=10, completed_count=8)
        config = SprintConfig(
            duration_days=14,
            ceremony_policy=CeremonyPolicyConfig(
                transition_threshold=0.8,
            ),
        )
        ctx = make_context(
            elapsed_seconds=SECONDS_PER_DAY,
            sprint_pct=0.8,
            total_tasks=10,
        )
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is SprintStatus.IN_REVIEW


# -- validate_strategy_config ------------------------------------------------


class TestValidateStrategyConfig:
    """validate_strategy_config() tests."""

    @pytest.mark.unit
    def test_valid_combined_config(self) -> None:
        strategy = HybridStrategy()
        strategy.validate_strategy_config(
            {
                "duration_days": 14,
                "every_n_completions": 10,
            }
        )

    @pytest.mark.unit
    def test_valid_task_only(self) -> None:
        strategy = HybridStrategy()
        strategy.validate_strategy_config({"every_n_completions": 5})

    @pytest.mark.unit
    def test_valid_calendar_only(self) -> None:
        strategy = HybridStrategy()
        strategy.validate_strategy_config({"duration_days": 14})

    @pytest.mark.unit
    def test_empty_config_valid(self) -> None:
        strategy = HybridStrategy()
        strategy.validate_strategy_config({})

    @pytest.mark.unit
    def test_unknown_keys_rejected(self) -> None:
        strategy = HybridStrategy()
        with pytest.raises(ValueError, match="Unknown config keys"):
            strategy.validate_strategy_config({"debounce": 5})

    @pytest.mark.unit
    def test_invalid_every_n_zero(self) -> None:
        strategy = HybridStrategy()
        with pytest.raises(ValueError, match="positive integer"):
            strategy.validate_strategy_config({"every_n_completions": 0})

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(0, id="zero"),
            pytest.param(-10, id="negative"),
            pytest.param(101, id="over_100"),
        ],
    )
    def test_invalid_sprint_percentage(self, value: int) -> None:
        """Out-of-range sprint_percentage values are rejected."""
        strategy = HybridStrategy()
        with pytest.raises(ValueError, match="between"):
            strategy.validate_strategy_config({"sprint_percentage": value})

    @pytest.mark.unit
    def test_invalid_duration_days_range(self) -> None:
        strategy = HybridStrategy()
        with pytest.raises(ValueError, match=r"1.*90"):
            strategy.validate_strategy_config({"duration_days": 0})

    @pytest.mark.unit
    def test_invalid_trigger_value(self) -> None:
        strategy = HybridStrategy()
        with pytest.raises(ValueError, match="Invalid trigger"):
            strategy.validate_strategy_config({"trigger": "unknown"})

    @pytest.mark.unit
    def test_valid_frequency_in_config(self) -> None:
        strategy = HybridStrategy()
        strategy.validate_strategy_config({"frequency": "daily"})

    @pytest.mark.unit
    def test_bool_rejected_as_duration_days(self) -> None:
        """bool is not accepted as int for duration_days."""
        strategy = HybridStrategy()
        with pytest.raises(ValueError, match="positive integer"):
            strategy.validate_strategy_config({"duration_days": True})

    @pytest.mark.unit
    def test_bool_rejected_as_every_n(self) -> None:
        """bool is not accepted as int for every_n_completions."""
        strategy = HybridStrategy()
        with pytest.raises(ValueError, match="positive integer"):
            strategy.validate_strategy_config({"every_n_completions": True})


# -- Lifecycle hooks ---------------------------------------------------------


class TestLifecycleHooks:
    """Lifecycle hook tests for state management."""

    @pytest.mark.unit
    async def test_on_sprint_activated_clears_state(self) -> None:
        strategy = HybridStrategy()
        ceremony = _make_ceremony(frequency=MeetingFrequency.DAILY, trigger=None)
        sprint = make_sprint()

        # Fire to create tracked state.
        ctx = make_context(elapsed_seconds=SECONDS_PER_DAY)
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True

        # Activate new sprint.
        await strategy.on_sprint_activated(sprint, SprintConfig())

        # Same elapsed fires again (state cleared).
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True

    @pytest.mark.unit
    async def test_on_sprint_deactivated_clears_state(self) -> None:
        strategy = HybridStrategy()
        ceremony = _make_ceremony(frequency=MeetingFrequency.DAILY, trigger=None)
        sprint = make_sprint()

        ctx = make_context(elapsed_seconds=SECONDS_PER_DAY)
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True

        await strategy.on_sprint_deactivated()

        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True
