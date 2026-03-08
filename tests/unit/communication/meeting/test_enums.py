"""Tests for meeting protocol enumerations."""

import pytest

from ai_company.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
    MeetingStatus,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestMeetingProtocolType:
    """Tests for MeetingProtocolType enum."""

    def test_round_robin_value(self) -> None:
        assert MeetingProtocolType.ROUND_ROBIN.value == "round_robin"

    def test_position_papers_value(self) -> None:
        assert MeetingProtocolType.POSITION_PAPERS.value == "position_papers"

    def test_structured_phases_value(self) -> None:
        assert MeetingProtocolType.STRUCTURED_PHASES.value == "structured_phases"

    def test_member_count(self) -> None:
        assert len(MeetingProtocolType) == 3

    def test_from_string(self) -> None:
        assert MeetingProtocolType("round_robin") is MeetingProtocolType.ROUND_ROBIN

    def test_is_str(self) -> None:
        assert isinstance(MeetingProtocolType.ROUND_ROBIN, str)


@pytest.mark.unit
class TestMeetingPhase:
    """Tests for MeetingPhase enum."""

    def test_all_phases_present(self) -> None:
        expected = {
            "agenda_broadcast",
            "round_robin_turn",
            "position_paper",
            "input_gathering",
            "discussion",
            "synthesis",
            "summary",
        }
        assert {p.value for p in MeetingPhase} == expected

    def test_member_count(self) -> None:
        assert len(MeetingPhase) == 7

    def test_is_str(self) -> None:
        assert isinstance(MeetingPhase.SUMMARY, str)


@pytest.mark.unit
class TestMeetingStatus:
    """Tests for MeetingStatus enum."""

    def test_all_statuses_present(self) -> None:
        expected = {
            "scheduled",
            "in_progress",
            "completed",
            "failed",
            "cancelled",
            "budget_exhausted",
        }
        assert {s.value for s in MeetingStatus} == expected

    def test_member_count(self) -> None:
        assert len(MeetingStatus) == 6

    def test_is_str(self) -> None:
        assert isinstance(MeetingStatus.COMPLETED, str)

    def test_from_string(self) -> None:
        assert MeetingStatus("budget_exhausted") is MeetingStatus.BUDGET_EXHAUSTED
