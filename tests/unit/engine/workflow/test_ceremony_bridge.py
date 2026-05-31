"""Tests for ceremony-to-meeting bridge functions."""

import pytest

from synthorg.communication.meeting.config import MeetingTypeConfig
from synthorg.communication.meeting.enums import MeetingProtocolType
from synthorg.communication.meeting.frequency import MeetingFrequency
from synthorg.engine.workflow.ceremony_bridge import (
    build_trigger_event_name,
    ceremony_to_meeting_type,
)
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyPolicyConfig,
    CeremonyStrategyType,
)
from synthorg.engine.workflow.sprint_config import SprintCeremonyConfig


class TestBuildTriggerEventName:
    """build_trigger_event_name() tests."""

    @pytest.mark.unit
    def test_basic(self) -> None:
        result = build_trigger_event_name("daily_standup", "sprint-1")
        assert result == "ceremony.daily_standup.sprint-1"

    @pytest.mark.unit
    def test_deterministic(self) -> None:
        a = build_trigger_event_name("retro", "sprint-42")
        b = build_trigger_event_name("retro", "sprint-42")
        assert a == b


class TestCeremonyToMeetingType:
    """ceremony_to_meeting_type() tests."""

    @pytest.mark.unit
    def test_frequency_based_ceremony(self) -> None:
        ceremony = SprintCeremonyConfig(
            name="daily_standup",
            protocol=MeetingProtocolType.ROUND_ROBIN,
            frequency=MeetingFrequency.DAILY,
            duration_tokens=2000,
            participants=("engineering",),
        )
        result = ceremony_to_meeting_type(ceremony, "sprint-1")
        assert isinstance(result, MeetingTypeConfig)
        assert result.name == "daily_standup"
        assert result.frequency is MeetingFrequency.DAILY
        assert result.trigger is None
        assert result.participants == ("engineering",)
        assert result.duration_tokens == 2000
        assert result.protocol_config.protocol is MeetingProtocolType.ROUND_ROBIN

    @pytest.mark.unit
    def test_trigger_only_ceremony(self) -> None:
        ceremony = SprintCeremonyConfig(
            name="retrospective",
            protocol=MeetingProtocolType.POSITION_PAPERS,
            policy_override=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.TASK_DRIVEN,
                strategy_config={"trigger": "sprint_end"},
            ),
            duration_tokens=3000,
        )
        result = ceremony_to_meeting_type(ceremony, "sprint-5")
        assert result.name == "retrospective"
        assert result.frequency is None
        assert result.trigger == "ceremony.retrospective.sprint-5"
        assert result.duration_tokens == 3000
        assert result.protocol_config.protocol is MeetingProtocolType.POSITION_PAPERS

    @pytest.mark.unit
    def test_hybrid_ceremony_uses_frequency_path(self) -> None:
        """When both frequency and policy_override are set, frequency wins."""
        ceremony = SprintCeremonyConfig(
            name="standup",
            protocol=MeetingProtocolType.ROUND_ROBIN,
            frequency=MeetingFrequency.PER_SPRINT_DAY,
            policy_override=CeremonyPolicyConfig(
                strategy=CeremonyStrategyType.HYBRID,
            ),
        )
        result = ceremony_to_meeting_type(ceremony, "sprint-1")
        assert result.frequency is MeetingFrequency.PER_SPRINT_DAY
        assert result.trigger is None

    @pytest.mark.unit
    def test_participants_carry_through(self) -> None:
        ceremony = SprintCeremonyConfig(
            name="review",
            protocol=MeetingProtocolType.STRUCTURED_PHASES,
            frequency=MeetingFrequency.WEEKLY,
            participants=("engineering", "product", "design"),
        )
        result = ceremony_to_meeting_type(ceremony, "sprint-1")
        assert result.participants == ("engineering", "product", "design")
