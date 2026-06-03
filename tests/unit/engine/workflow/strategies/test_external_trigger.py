"""Tests for the ExternalTriggerStrategy implementation."""

import pytest
from typeguard import suppress_type_checks

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
from synthorg.engine.workflow.strategies.external_trigger import (
    ExternalTriggerStrategy,
)
from synthorg.engine.workflow.velocity_types import VelocityCalcType

from .conftest import make_context, make_sprint


def _make_ceremony(
    name: str = "code_review",
    on_external: str | None = None,
) -> SprintCeremonyConfig:
    """Create a ceremony config with external-trigger policy override."""
    config: dict[str, object] = {}
    if on_external is not None:
        config["on_external"] = on_external
    return SprintCeremonyConfig(
        name=name,
        protocol=MeetingProtocolType.ROUND_ROBIN,
        policy_override=CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.EXTERNAL_TRIGGER,
            strategy_config=config,
        ),
    )


def _make_sprint_config(
    sources: list[dict[str, object]] | None = None,
    transition_event: str | None = None,
) -> SprintConfig:
    """Create a SprintConfig with external-trigger policy."""
    config: dict[str, object] = {}
    if sources is not None:
        config["sources"] = sources
    if transition_event is not None:
        config["transition_event"] = transition_event
    return SprintConfig(
        ceremony_policy=CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.EXTERNAL_TRIGGER,
            strategy_config=config,
        ),
    )


class TestExternalTriggerProtocol:
    """Verify ExternalTriggerStrategy satisfies the protocol."""

    @pytest.mark.unit
    def test_is_protocol_instance(self) -> None:
        strategy = ExternalTriggerStrategy()
        assert isinstance(strategy, CeremonySchedulingStrategy)

    @pytest.mark.unit
    def test_strategy_type(self) -> None:
        assert (
            ExternalTriggerStrategy().strategy_type
            is CeremonyStrategyType.EXTERNAL_TRIGGER
        )

    @pytest.mark.unit
    def test_default_velocity_calculator(self) -> None:
        assert (
            ExternalTriggerStrategy().get_default_velocity_calculator()
            is VelocityCalcType.POINTS_PER_SPRINT
        )


class TestShouldFireCeremony:
    """should_fire_ceremony() tests."""

    @pytest.mark.unit
    async def test_fires_when_external_event_in_context(self) -> None:
        """Ceremony fires when matching event is in context.external_events."""
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        ceremony = _make_ceremony(on_external="pr_merged")
        ctx = make_context(external_events=("pr_merged",))

        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True

    @pytest.mark.unit
    async def test_fires_when_external_event_in_received_buffer(self) -> None:
        """Ceremony fires when matching event was received via hook."""
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(sprint, "pr_merged", {})

        ceremony = _make_ceremony(on_external="pr_merged")
        ctx = make_context()

        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True

    @pytest.mark.unit
    async def test_does_not_fire_without_matching_event(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        ceremony = _make_ceremony(on_external="pr_merged")
        ctx = make_context(external_events=("deploy_completed",))

        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_does_not_fire_with_no_on_external_config(self) -> None:
        """No on_external means the ceremony has nothing to match."""
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        ceremony = _make_ceremony()  # no on_external
        ctx = make_context(external_events=("pr_merged",))

        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_no_policy_override_returns_false(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        ceremony = SprintCeremonyConfig(
            name="standup",
            protocol=MeetingProtocolType.ROUND_ROBIN,
            frequency=MeetingFrequency.DAILY,
        )
        ctx = make_context(external_events=("pr_merged",))

        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_ignores_non_matching_events_in_context(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        ceremony = _make_ceremony(on_external="release_published")
        ctx = make_context(
            external_events=("pr_merged", "deploy_completed", "ci_passed"),
        )

        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_fires_with_matching_among_multiple_events(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        ceremony = _make_ceremony(on_external="deploy_completed")
        ctx = make_context(
            external_events=("pr_merged", "deploy_completed", "ci_passed"),
        )

        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True


class TestShouldTransitionSprint:
    """should_transition_sprint() tests."""

    @pytest.mark.unit
    async def test_transitions_on_context_external_event(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(transition_event="deploy_complete")
        await strategy.on_sprint_activated(sprint, config)

        ctx = make_context(external_events=("deploy_complete",))
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is SprintStatus.IN_REVIEW

    @pytest.mark.unit
    async def test_transitions_on_buffered_external_event(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(transition_event="deploy_complete")
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(sprint, "deploy_complete", {})

        ctx = make_context()
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is SprintStatus.IN_REVIEW

    @pytest.mark.unit
    async def test_does_not_transition_without_matching_event(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(transition_event="deploy_complete")
        await strategy.on_sprint_activated(sprint, config)

        ctx = make_context(external_events=("pr_merged",))
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is None

    @pytest.mark.unit
    def test_does_not_transition_non_active(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint(status=SprintStatus.PLANNING)
        config = _make_sprint_config(transition_event="deploy_complete")
        ctx = make_context(external_events=("deploy_complete",))
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is None

    @pytest.mark.unit
    def test_no_transition_event_returns_none(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        ctx = make_context(external_events=("deploy_complete",))
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is None

    @pytest.mark.unit
    def test_transition_with_strategy_config_none(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = SprintConfig(
            ceremony_policy=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.EXTERNAL_TRIGGER,
                strategy_config=None,
            ),
        )
        ctx = make_context()
        result = strategy.should_transition_sprint(sprint, config, ctx)
        assert result is None


class TestLifecycleHooks:
    """Lifecycle hook tests."""

    @pytest.mark.unit
    async def test_on_sprint_activated_clears_state(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()

        await strategy.on_sprint_activated(sprint, config)
        await strategy.on_external_event(sprint, "pr_merged", {})

        # Re-activate -- state should reset
        await strategy.on_sprint_activated(sprint, config)

        ceremony = _make_ceremony(on_external="pr_merged")
        ctx = make_context()
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_on_sprint_activated_reads_sources(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config(
            sources=[
                {"type": "webhook", "endpoint": "/hooks/ceremony"},
                {"type": "git_event", "events": ["push", "tag"]},
            ],
        )
        await strategy.on_sprint_activated(sprint, config)

        assert len(strategy._sources) == 2

    @pytest.mark.unit
    async def test_on_sprint_deactivated_clears_state(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()

        await strategy.on_sprint_activated(sprint, config)
        await strategy.on_external_event(sprint, "pr_merged", {})
        await strategy.on_sprint_deactivated()

        ceremony = _make_ceremony(on_external="pr_merged")
        ctx = make_context()
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_on_external_event_buffers_event(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(sprint, "pr_merged", {})
        assert "pr_merged" in strategy._received_events
        await strategy.on_external_event(sprint, "deploy_completed", {})
        assert "deploy_completed" in strategy._received_events

    @pytest.mark.unit
    async def test_on_external_event_rejects_invalid_name(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        # Empty string
        await strategy.on_external_event(sprint, "", {})
        assert len(strategy._received_events) == 0

        # Whitespace only
        await strategy.on_external_event(sprint, "   ", {})
        assert len(strategy._received_events) == 0

        # Too long
        await strategy.on_external_event(sprint, "x" * 200, {})
        assert len(strategy._received_events) == 0

    @pytest.mark.unit
    async def test_on_external_event_caps_at_max_received(self) -> None:
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        # Fill to capacity
        for i in range(256):
            await strategy.on_external_event(sprint, f"event_{i}", {})

        assert len(strategy._received_events) == 256

        # One more should be rejected
        await strategy.on_external_event(sprint, "overflow_event", {})
        assert "overflow_event" not in strategy._received_events

    @pytest.mark.unit
    async def test_task_hooks_are_noop(self) -> None:
        """Task lifecycle hooks do not affect external event state."""
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        ctx = make_context()
        await strategy.on_task_completed(sprint, "t-1", 3.0, ctx)
        await strategy.on_task_added(sprint, "t-2")
        await strategy.on_task_blocked(sprint, "t-3")
        await strategy.on_budget_updated(sprint, 0.5)

        assert len(strategy._received_events) == 0

    @pytest.mark.unit
    async def test_whitespace_stripped_event_matching(self) -> None:
        """Whitespace-padded events match after stripping."""
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        # Buffer event with leading/trailing whitespace
        await strategy.on_external_event(sprint, "  pr_merged  ", {})

        # Ceremony with clean name should match stripped buffer
        ceremony = _make_ceremony(on_external="pr_merged")
        ctx = make_context()
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True

    @pytest.mark.unit
    async def test_on_sprint_activated_filters_non_dict_sources(self) -> None:
        """Non-dict entries in sources list are silently filtered."""
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = SprintConfig(
            ceremony_policy=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.EXTERNAL_TRIGGER,
                strategy_config={
                    "sources": [
                        {"type": "webhook"},
                        "not_a_dict",
                        42,
                        {"type": "git_event"},
                    ],
                },
            ),
        )
        await strategy.on_sprint_activated(sprint, config)

        # Only the 2 dict entries should survive
        assert len(strategy._sources) == 2


class TestValidateStrategyConfig:
    """validate_strategy_config() tests."""

    @pytest.mark.unit
    def test_valid_config(self) -> None:
        strategy = ExternalTriggerStrategy()
        strategy.validate_strategy_config({"transition_event": "deploy_complete"})

    @pytest.mark.unit
    def test_valid_config_with_all_keys(self) -> None:
        strategy = ExternalTriggerStrategy()
        strategy.validate_strategy_config(
            {
                "transition_event": "deploy_complete",
                "sources": [
                    {"type": "webhook"},
                    {"type": "git_event"},
                ],
            }
        )

    @pytest.mark.unit
    def test_empty_config_valid(self) -> None:
        strategy = ExternalTriggerStrategy()
        strategy.validate_strategy_config({})

    @pytest.mark.unit
    def test_unknown_keys_rejected(self) -> None:
        strategy = ExternalTriggerStrategy()
        with pytest.raises(ValueError, match="Unknown config keys"):
            strategy.validate_strategy_config({"unknown_key": 42})

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("config", "match"),
        [
            ({"on_external": "pr_merged"}, "Unknown config keys"),
            ({"transition_event": ""}, "non-empty string"),
            ({"transition_event": 42}, "non-empty string"),
            ({"transition_event": "x" * 200}, "<= 128"),
            ({"sources": "not_a_list"}, "must be a list"),
            ({"sources": [42]}, "must be a dict"),
            ({"sources": [{"no_type": "x"}]}, "must have a 'type'"),
            ({"sources": [{"type": 42}]}, "must be a string"),
            ({"sources": [{"type": ["list"]}]}, "must be a string"),
            ({"sources": [{"type": "invalid"}]}, "must be one of"),
            (
                {"sources": [{"type": "webhook"}] * 21},
                "at most 20",
            ),
        ],
        ids=[
            "on_external_sprint_level_rejected",
            "transition_event_empty",
            "transition_event_non_string",
            "transition_event_too_long",
            "sources_not_list",
            "sources_non_dict_entry",
            "sources_no_type",
            "sources_type_int",
            "sources_type_list",
            "sources_invalid_type",
            "sources_too_many",
        ],
    )
    def test_invalid_config_rejected(
        self,
        config: dict[str, object],
        match: str,
    ) -> None:
        strategy = ExternalTriggerStrategy()
        with pytest.raises(ValueError, match=match):
            strategy.validate_strategy_config(config)


class TestEdgeTriggering:
    """Edge-triggered matching semantics."""

    @pytest.mark.unit
    async def test_buffered_event_fires_once_per_occurrence(self) -> None:
        """Same buffered event does not re-fire on repeated evaluation."""
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(sprint, "pr_merged", {})

        ceremony = _make_ceremony(on_external="pr_merged")
        ctx = make_context()

        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True
        # Second evaluation without new event -- should NOT fire
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_new_occurrence_re_enables_firing(self) -> None:
        """A new on_external_event call re-enables the ceremony."""
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(sprint, "pr_merged", {})
        ceremony = _make_ceremony(on_external="pr_merged")
        ctx = make_context()

        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

        # New occurrence
        await strategy.on_external_event(sprint, "pr_merged", {})
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is False

    @pytest.mark.unit
    async def test_context_events_always_fire(self) -> None:
        """Context events are one-shot by nature -- always fire."""
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        ceremony = _make_ceremony(on_external="pr_merged")
        ctx = make_context(external_events=("pr_merged",))

        # Context events fire every time they appear
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True
        assert strategy.should_fire_ceremony(ceremony, sprint, ctx) is True

    @pytest.mark.unit
    async def test_different_ceremonies_fire_independently(self) -> None:
        """Two ceremonies matching the same event fire independently."""
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        await strategy.on_external_event(sprint, "pr_merged", {})

        ceremony_a = _make_ceremony(name="review", on_external="pr_merged")
        ceremony_b = _make_ceremony(name="retro", on_external="pr_merged")
        ctx = make_context()

        assert strategy.should_fire_ceremony(ceremony_a, sprint, ctx) is True
        assert strategy.should_fire_ceremony(ceremony_b, sprint, ctx) is True
        # Both consumed -- neither fires again
        assert strategy.should_fire_ceremony(ceremony_a, sprint, ctx) is False
        assert strategy.should_fire_ceremony(ceremony_b, sprint, ctx) is False


class TestBoundaryInputs:
    """Boundary input tests for coverage gaps."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "event_name",
        [None, 123, ["list"]],
        ids=["none", "int", "list"],
    )
    async def test_on_external_event_rejects_non_string(
        self,
        event_name: object,
    ) -> None:
        """Non-string event names are rejected without buffering."""
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = _make_sprint_config()
        await strategy.on_sprint_activated(sprint, config)

        with suppress_type_checks():
            await strategy.on_external_event(sprint, event_name, {})  # type: ignore[arg-type]
        assert len(strategy._received_events) == 0

    @pytest.mark.unit
    async def test_on_sprint_activated_with_strategy_config_none(self) -> None:
        """on_sprint_activated handles strategy_config=None gracefully."""
        strategy = ExternalTriggerStrategy()
        sprint = make_sprint()
        config = SprintConfig(
            ceremony_policy=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.EXTERNAL_TRIGGER,
                strategy_config=None,
            ),
        )
        await strategy.on_sprint_activated(sprint, config)
        assert len(strategy._sources) == 0
        assert len(strategy._received_events) == 0
