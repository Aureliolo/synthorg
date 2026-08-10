"""Tests for sprint configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.communication.meeting.config import MeetingProtocolConfig
from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyPolicyConfig,
    CeremonyStrategyType,
)
from synthorg.engine.workflow.sprint_config import (
    SprintCeremonyConfig,
    SprintConfig,
)

# ── SprintCeremonyConfig ───────────────────────────────────────


class TestSprintCeremonyConfig:
    """SprintCeremonyConfig validates ceremony definitions."""

    @pytest.mark.unit
    def test_basic_ceremony(self) -> None:
        ceremony = SprintCeremonyConfig(
            name="sprint_planning",
            protocol=MeetingProtocolType.STRUCTURED_PHASES,
            frequency=MeetingFrequency.BI_WEEKLY,
        )
        assert ceremony.name == "sprint_planning"
        assert ceremony.duration_tokens == 5000

    @pytest.mark.unit
    def test_custom_duration_tokens(self) -> None:
        ceremony = SprintCeremonyConfig(
            name="daily_standup",
            protocol=MeetingProtocolType.ROUND_ROBIN,
            frequency=MeetingFrequency.PER_SPRINT_DAY,
            duration_tokens=2000,
        )
        assert ceremony.duration_tokens == 2000

    @pytest.mark.unit
    def test_duration_token_bounds(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal"):
            SprintCeremonyConfig(
                name="bad",
                protocol=MeetingProtocolType.ROUND_ROBIN,
                frequency=MeetingFrequency.DAILY,
                duration_tokens=50,
            )
        with pytest.raises(ValueError, match="less than or equal"):
            SprintCeremonyConfig(
                name="bad",
                protocol=MeetingProtocolType.ROUND_ROBIN,
                frequency=MeetingFrequency.DAILY,
                duration_tokens=100_000,
            )

    @pytest.mark.unit
    def test_no_frequency_no_policy_override_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one of"):
            SprintCeremonyConfig(
                name="bad",
                protocol=MeetingProtocolType.ROUND_ROBIN,
            )

    @pytest.mark.unit
    def test_policy_override_only_accepted(self) -> None:
        ceremony = SprintCeremonyConfig(
            name="trigger_only",
            protocol=MeetingProtocolType.ROUND_ROBIN,
            policy_override=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.TASK_DRIVEN,
                strategy_config={"trigger": "sprint_start"},
            ),
        )
        assert ceremony.frequency is None
        assert ceremony.policy_override is not None

    @pytest.mark.unit
    def test_both_frequency_and_policy_override_accepted(self) -> None:
        ceremony = SprintCeremonyConfig(
            name="hybrid",
            protocol=MeetingProtocolType.ROUND_ROBIN,
            frequency=MeetingFrequency.DAILY,
            policy_override=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.HYBRID,
            ),
        )
        assert ceremony.frequency is MeetingFrequency.DAILY
        assert ceremony.policy_override is not None

    @pytest.mark.unit
    def test_participants_default_empty(self) -> None:
        ceremony = SprintCeremonyConfig(
            name="test",
            protocol=MeetingProtocolType.ROUND_ROBIN,
            frequency=MeetingFrequency.WEEKLY,
        )
        assert ceremony.participants == ()


class TestSprintCeremonyProtocolConfig:
    """``protocol`` and ``protocol_config.protocol`` cannot disagree."""

    @pytest.mark.unit
    def test_default_config_adopts_the_ceremony_protocol(self) -> None:
        ceremony = SprintCeremonyConfig(
            name="retrospective",
            protocol=MeetingProtocolType.POSITION_PAPERS,
            frequency=MeetingFrequency.BI_WEEKLY,
        )
        assert ceremony.protocol_config.protocol is MeetingProtocolType.POSITION_PAPERS

    @pytest.mark.unit
    def test_an_unreadable_shape_defers_to_pydantic(self) -> None:
        """The validator declines to guess, so the type error still lands."""
        with pytest.raises(ValidationError):
            SprintCeremonyConfig.model_validate(
                {
                    "name": "retrospective",
                    "protocol": "position_papers",
                    "frequency": "bi_weekly",
                    "protocol_config": "structured_phases",
                }
            )

    @pytest.mark.unit
    def test_an_explicit_none_is_rejected_not_filled_in(self) -> None:
        """Omitted and explicitly-null are different, and null is a type error.

        Filling in an explicit ``None`` would accept a config the field
        declaration refuses, so the author never learns it was wrong.
        """
        with pytest.raises(ValidationError):
            SprintCeremonyConfig(
                name="retrospective",
                protocol=MeetingProtocolType.POSITION_PAPERS,
                frequency=MeetingFrequency.BI_WEEKLY,
                protocol_config=None,  # type: ignore[arg-type]
            )

    @pytest.mark.unit
    def test_sub_config_without_protocol_adopts_the_ceremony_protocol(self) -> None:
        """The terse form stays terse: name the protocol once, tune the rest."""
        ceremony = SprintCeremonyConfig.model_validate(
            {
                "name": "sprint_planning",
                "protocol": "structured_phases",
                "frequency": "bi_weekly",
                "protocol_config": {
                    "structured_phases": {"max_discussion_tokens": 2000},
                },
            }
        )
        assert (
            ceremony.protocol_config.protocol is MeetingProtocolType.STRUCTURED_PHASES
        )
        assert ceremony.protocol_config.structured_phases.max_discussion_tokens == 2000

    @pytest.mark.unit
    def test_agreeing_protocol_accepted(self) -> None:
        ceremony = SprintCeremonyConfig(
            name="planning",
            protocol=MeetingProtocolType.STRUCTURED_PHASES,
            frequency=MeetingFrequency.BI_WEEKLY,
            protocol_config=MeetingProtocolConfig(
                protocol=MeetingProtocolType.STRUCTURED_PHASES,
            ),
        )
        assert (
            ceremony.protocol_config.protocol is MeetingProtocolType.STRUCTURED_PHASES
        )

    @pytest.mark.unit
    @pytest.mark.parametrize("as_model", [True, False])
    def test_disagreeing_protocol_rejected(self, as_model: bool) -> None:
        """A silent override is what makes config unreachable; refuse instead."""
        config: MeetingProtocolConfig | dict[str, str] = (
            MeetingProtocolConfig(protocol=MeetingProtocolType.STRUCTURED_PHASES)
            if as_model
            else {"protocol": "structured_phases"}
        )
        with pytest.raises(ValidationError, match="round_robin"):
            SprintCeremonyConfig.model_validate(
                {
                    "name": "planning",
                    "protocol": "round_robin",
                    "frequency": "weekly",
                    "protocol_config": config,
                }
            )

    @pytest.mark.unit
    def test_round_trip_is_stable(self) -> None:
        """A dumped-and-reloaded ceremony validates unchanged.

        ``model_dump`` writes the nested ``protocol`` out explicitly, so a
        validator that rejected an agreeing value would break every persisted
        config on reload.
        """
        ceremony = SprintCeremonyConfig(
            name="planning",
            protocol=MeetingProtocolType.STRUCTURED_PHASES,
            frequency=MeetingFrequency.BI_WEEKLY,
        )
        assert SprintCeremonyConfig.model_validate(ceremony.model_dump()) == ceremony


# ── SprintConfig ───────────────────────────────────────────────


class TestSprintConfig:
    """SprintConfig validates sprint workflow settings."""

    @pytest.mark.unit
    def test_default_config(self) -> None:
        config = SprintConfig()
        assert config.duration_days == 14
        assert config.max_tasks_per_sprint == 50
        assert config.velocity_window == 3
        assert len(config.ceremonies) == 4

    @pytest.mark.unit
    def test_default_ceremony_names(self) -> None:
        config = SprintConfig()
        names = {c.name for c in config.ceremonies}
        assert names == {
            "sprint_planning",
            "daily_standup",
            "sprint_review",
            "retrospective",
        }

    @pytest.mark.unit
    def test_default_ceremony_protocols(self) -> None:
        config = SprintConfig()
        by_name = {c.name: c for c in config.ceremonies}
        assert (
            by_name["sprint_planning"].protocol == MeetingProtocolType.STRUCTURED_PHASES
        )
        assert by_name["daily_standup"].protocol == MeetingProtocolType.ROUND_ROBIN
        assert by_name["sprint_review"].protocol == MeetingProtocolType.ROUND_ROBIN
        assert by_name["retrospective"].protocol == MeetingProtocolType.POSITION_PAPERS

    @pytest.mark.unit
    def test_default_ceremony_frequencies(self) -> None:
        config = SprintConfig()
        by_name = {c.name: c for c in config.ceremonies}
        assert by_name["daily_standup"].frequency == MeetingFrequency.PER_SPRINT_DAY
        assert by_name["sprint_planning"].frequency == MeetingFrequency.BI_WEEKLY

    @pytest.mark.unit
    def test_duplicate_ceremony_names_rejected(self) -> None:
        with pytest.raises(ValueError, match="Duplicate ceremony names"):
            SprintConfig(
                ceremonies=(
                    SprintCeremonyConfig(
                        name="standup",
                        protocol=MeetingProtocolType.ROUND_ROBIN,
                        frequency=MeetingFrequency.DAILY,
                    ),
                    SprintCeremonyConfig(
                        name="standup",
                        protocol=MeetingProtocolType.POSITION_PAPERS,
                        frequency=MeetingFrequency.DAILY,
                    ),
                ),
            )

    @pytest.mark.unit
    def test_empty_ceremonies_allowed(self) -> None:
        config = SprintConfig(ceremonies=())
        assert config.ceremonies == ()

    @pytest.mark.unit
    def test_custom_config(self) -> None:
        config = SprintConfig(
            duration_days=7,
            max_tasks_per_sprint=20,
            velocity_window=5,
            ceremonies=(),
        )
        assert config.duration_days == 7
        assert config.max_tasks_per_sprint == 20
        assert config.velocity_window == 5

    @pytest.mark.unit
    def test_duration_bounds(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal"):
            SprintConfig(duration_days=0)
        with pytest.raises(ValueError, match="less than or equal"):
            SprintConfig(duration_days=91)

    @pytest.mark.unit
    def test_default_ceremony_policy(self) -> None:
        config = SprintConfig()
        policy = config.ceremony_policy
        assert policy.strategy is CeremonyStrategyType.TASK_DRIVEN
        assert policy.auto_transition is True
        assert policy.transition_threshold == 1.0

    @pytest.mark.unit
    def test_custom_ceremony_policy(self) -> None:
        custom = CeremonyPolicyConfig(
            strategy=CeremonyStrategyType.CALENDAR,
            auto_transition=False,
        )
        config = SprintConfig(ceremony_policy=custom)
        assert config.ceremony_policy.strategy is CeremonyStrategyType.CALENDAR
        assert config.ceremony_policy.auto_transition is False

    @pytest.mark.unit
    def test_velocity_window_bounds(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal"):
            SprintConfig(velocity_window=0)
        with pytest.raises(ValueError, match="less than or equal"):
            SprintConfig(velocity_window=21)
