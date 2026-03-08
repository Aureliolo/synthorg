"""Tests for meeting protocol domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_company.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
    MeetingStatus,
)
from ai_company.communication.meeting.models import (
    ActionItem,
    AgentResponse,
    MeetingAgenda,
    MeetingAgendaItem,
    MeetingContribution,
    MeetingMinutes,
    MeetingRecord,
)
from ai_company.core.enums import Priority

_NOW = datetime(2026, 3, 8, 10, 0, tzinfo=UTC)
_LATER = datetime(2026, 3, 8, 10, 30, tzinfo=UTC)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestAgentResponse:
    """Tests for AgentResponse model."""

    def test_basic_creation(self) -> None:
        resp = AgentResponse(agent_id="agent-a", content="Hello")
        assert resp.agent_id == "agent-a"
        assert resp.content == "Hello"
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0
        assert resp.cost_usd == 0.0

    def test_with_token_counts(self) -> None:
        resp = AgentResponse(
            agent_id="agent-b",
            content="Analysis",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.002,
        )
        assert resp.input_tokens == 100
        assert resp.output_tokens == 50
        assert resp.cost_usd == 0.002

    def test_frozen(self) -> None:
        resp = AgentResponse(agent_id="agent-a", content="Hi")
        with pytest.raises(ValidationError):
            resp.content = "Changed"  # type: ignore[misc]

    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentResponse(agent_id="a", content="x", input_tokens=-1)

    def test_blank_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentResponse(agent_id="  ", content="x")


@pytest.mark.unit
class TestMeetingAgendaItem:
    """Tests for MeetingAgendaItem model."""

    def test_minimal(self) -> None:
        item = MeetingAgendaItem(title="API Design")
        assert item.title == "API Design"
        assert item.description == ""
        assert item.presenter_id is None

    def test_full(self) -> None:
        item = MeetingAgendaItem(
            title="Backend Update",
            description="Discuss new endpoints",
            presenter_id="agent-backend",
        )
        assert item.presenter_id == "agent-backend"

    def test_blank_title_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MeetingAgendaItem(title="")


@pytest.mark.unit
class TestMeetingAgenda:
    """Tests for MeetingAgenda model."""

    def test_minimal(self) -> None:
        agenda = MeetingAgenda(title="Sprint Planning")
        assert agenda.title == "Sprint Planning"
        assert agenda.context == ""
        assert agenda.items == ()

    def test_with_items(self) -> None:
        items = (
            MeetingAgendaItem(title="Item 1"),
            MeetingAgendaItem(title="Item 2"),
        )
        agenda = MeetingAgenda(
            title="Standup",
            context="Daily sync",
            items=items,
        )
        assert len(agenda.items) == 2


@pytest.mark.unit
class TestMeetingContribution:
    """Tests for MeetingContribution model."""

    def test_creation(self) -> None:
        contrib = MeetingContribution(
            agent_id="agent-a",
            content="My input",
            phase=MeetingPhase.ROUND_ROBIN_TURN,
            turn_number=1,
            timestamp=_NOW,
        )
        assert contrib.agent_id == "agent-a"
        assert contrib.phase == MeetingPhase.ROUND_ROBIN_TURN
        assert contrib.turn_number == 1
        assert contrib.input_tokens == 0

    def test_negative_turn_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MeetingContribution(
                agent_id="a",
                content="x",
                phase=MeetingPhase.SUMMARY,
                turn_number=-1,
                timestamp=_NOW,
            )


@pytest.mark.unit
class TestActionItem:
    """Tests for ActionItem model."""

    def test_defaults(self) -> None:
        item = ActionItem(description="Fix the bug")
        assert item.description == "Fix the bug"
        assert item.assignee_id is None
        assert item.priority == Priority.MEDIUM

    def test_with_assignee(self) -> None:
        item = ActionItem(
            description="Deploy",
            assignee_id="agent-ops",
            priority=Priority.HIGH,
        )
        assert item.assignee_id == "agent-ops"
        assert item.priority == Priority.HIGH

    def test_blank_description_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ActionItem(description="")


@pytest.mark.unit
class TestMeetingMinutes:
    """Tests for MeetingMinutes model."""

    def _make_minutes(self, **overrides: object) -> MeetingMinutes:
        defaults: dict[str, object] = {
            "meeting_id": "m-1",
            "protocol_type": MeetingProtocolType.ROUND_ROBIN,
            "leader_id": "leader",
            "participant_ids": ("agent-a", "agent-b"),
            "agenda": MeetingAgenda(title="Test"),
            "started_at": _NOW,
            "ended_at": _LATER,
        }
        defaults.update(overrides)
        return MeetingMinutes(**defaults)  # type: ignore[arg-type]

    def test_basic_creation(self) -> None:
        minutes = self._make_minutes()
        assert minutes.meeting_id == "m-1"
        assert minutes.summary == ""
        assert minutes.decisions == ()
        assert minutes.action_items == ()
        assert minutes.conflicts_detected is False

    def test_total_tokens_computed(self) -> None:
        contrib = MeetingContribution(
            agent_id="agent-a",
            content="Input",
            phase=MeetingPhase.ROUND_ROBIN_TURN,
            turn_number=0,
            input_tokens=100,
            output_tokens=50,
            timestamp=_NOW,
        )
        minutes = self._make_minutes(
            contributions=(contrib,),
            total_input_tokens=100,
            total_output_tokens=50,
        )
        assert minutes.total_tokens == 150

    def test_total_tokens_zero_by_default(self) -> None:
        minutes = self._make_minutes()
        assert minutes.total_tokens == 0

    def test_ended_before_started_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ended_at must not be before"):
            self._make_minutes(
                started_at=_LATER,
                ended_at=_NOW,
            )

    def test_same_start_end_accepted(self) -> None:
        minutes = self._make_minutes(started_at=_NOW, ended_at=_NOW)
        assert minutes.started_at == minutes.ended_at

    def test_frozen(self) -> None:
        minutes = self._make_minutes()
        with pytest.raises(ValidationError):
            minutes.summary = "Changed"  # type: ignore[misc]

    def test_with_contributions(self) -> None:
        contrib = MeetingContribution(
            agent_id="agent-a",
            content="Input",
            phase=MeetingPhase.ROUND_ROBIN_TURN,
            turn_number=0,
            input_tokens=10,
            output_tokens=20,
            timestamp=_NOW,
        )
        minutes = self._make_minutes(
            contributions=(contrib,),
            total_input_tokens=10,
            total_output_tokens=20,
        )
        assert len(minutes.contributions) == 1

    def test_with_action_items(self) -> None:
        action = ActionItem(description="Deploy to prod")
        minutes = self._make_minutes(action_items=(action,))
        assert len(minutes.action_items) == 1

    def test_duplicate_participant_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="participant_ids"):
            self._make_minutes(
                participant_ids=("agent-a", "agent-b", "agent-a"),
            )

    def test_leader_in_participants_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="leader_id must not be in participant_ids",
        ):
            self._make_minutes(
                leader_id="agent-a",
                participant_ids=("agent-a", "agent-b"),
            )


@pytest.mark.unit
class TestMeetingRecord:
    """Tests for MeetingRecord model."""

    def _make_minutes(self) -> MeetingMinutes:
        return MeetingMinutes(
            meeting_id="m-1",
            protocol_type=MeetingProtocolType.ROUND_ROBIN,
            leader_id="leader",
            participant_ids=("agent-a",),
            agenda=MeetingAgenda(title="Test"),
            started_at=_NOW,
            ended_at=_LATER,
        )

    def test_completed_requires_minutes(self) -> None:
        with pytest.raises(ValidationError, match="minutes are required"):
            MeetingRecord(
                meeting_id="m-1",
                meeting_type_name="standup",
                protocol_type=MeetingProtocolType.ROUND_ROBIN,
                status=MeetingStatus.COMPLETED,
                token_budget=2000,
            )

    def test_completed_with_minutes(self) -> None:
        record = MeetingRecord(
            meeting_id="m-1",
            meeting_type_name="standup",
            protocol_type=MeetingProtocolType.ROUND_ROBIN,
            status=MeetingStatus.COMPLETED,
            minutes=self._make_minutes(),
            token_budget=2000,
        )
        assert record.status == MeetingStatus.COMPLETED
        assert record.minutes is not None

    def test_failed_requires_error_message(self) -> None:
        with pytest.raises(ValidationError, match="error_message is required"):
            MeetingRecord(
                meeting_id="m-1",
                meeting_type_name="standup",
                protocol_type=MeetingProtocolType.ROUND_ROBIN,
                status=MeetingStatus.FAILED,
                token_budget=2000,
            )

    def test_budget_exhausted_requires_error_message(self) -> None:
        with pytest.raises(ValidationError, match="error_message is required"):
            MeetingRecord(
                meeting_id="m-1",
                meeting_type_name="standup",
                protocol_type=MeetingProtocolType.ROUND_ROBIN,
                status=MeetingStatus.BUDGET_EXHAUSTED,
                token_budget=2000,
            )

    def test_failed_with_error(self) -> None:
        record = MeetingRecord(
            meeting_id="m-1",
            meeting_type_name="standup",
            protocol_type=MeetingProtocolType.ROUND_ROBIN,
            status=MeetingStatus.FAILED,
            error_message="Agent timeout",
            token_budget=2000,
        )
        assert record.error_message == "Agent timeout"

    def test_scheduled_no_minutes_required(self) -> None:
        record = MeetingRecord(
            meeting_id="m-1",
            meeting_type_name="standup",
            protocol_type=MeetingProtocolType.ROUND_ROBIN,
            status=MeetingStatus.SCHEDULED,
            token_budget=2000,
        )
        assert record.minutes is None
        assert record.error_message is None

    def test_token_budget_gt_0(self) -> None:
        with pytest.raises(ValidationError, match="greater than 0"):
            MeetingRecord(
                meeting_id="m-1",
                meeting_type_name="standup",
                protocol_type=MeetingProtocolType.ROUND_ROBIN,
                status=MeetingStatus.SCHEDULED,
                token_budget=0,
            )

    def test_completed_with_error_message_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="error_message must be None",
        ):
            MeetingRecord(
                meeting_id="m-1",
                meeting_type_name="standup",
                protocol_type=MeetingProtocolType.ROUND_ROBIN,
                status=MeetingStatus.COMPLETED,
                minutes=self._make_minutes(),
                error_message="Should not be here",
                token_budget=2000,
            )

    def test_failed_with_minutes_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="minutes must be None",
        ):
            MeetingRecord(
                meeting_id="m-1",
                meeting_type_name="standup",
                protocol_type=MeetingProtocolType.ROUND_ROBIN,
                status=MeetingStatus.FAILED,
                minutes=self._make_minutes(),
                error_message="Something failed",
                token_budget=2000,
            )

    def test_budget_exhausted_with_minutes_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="minutes must be None",
        ):
            MeetingRecord(
                meeting_id="m-1",
                meeting_type_name="standup",
                protocol_type=MeetingProtocolType.ROUND_ROBIN,
                status=MeetingStatus.BUDGET_EXHAUSTED,
                minutes=self._make_minutes(),
                error_message="Budget ran out",
                token_budget=2000,
            )

    def test_blank_error_message_rejected(self) -> None:
        """Whitespace-only error_message is rejected via NotBlankStr."""
        with pytest.raises(ValidationError):
            MeetingRecord(
                meeting_id="m-1",
                meeting_type_name="standup",
                protocol_type=MeetingProtocolType.ROUND_ROBIN,
                status=MeetingStatus.FAILED,
                error_message="   ",
                token_budget=2000,
            )

    def test_frozen(self) -> None:
        record = MeetingRecord(
            meeting_id="m-1",
            meeting_type_name="standup",
            protocol_type=MeetingProtocolType.ROUND_ROBIN,
            status=MeetingStatus.SCHEDULED,
            token_budget=2000,
        )
        with pytest.raises(ValidationError):
            record.status = MeetingStatus.COMPLETED  # type: ignore[misc]


@pytest.mark.unit
class TestMeetingMinutesTokenAggregates:
    """Tests for MeetingMinutes token aggregate validation."""

    def test_mismatched_input_tokens_rejected(self) -> None:
        """total_input_tokens != sum of contributions raises."""
        contrib = MeetingContribution(
            agent_id="agent-a",
            content="Input",
            phase=MeetingPhase.ROUND_ROBIN_TURN,
            turn_number=0,
            input_tokens=10,
            output_tokens=20,
            timestamp=_NOW,
        )
        with pytest.raises(ValidationError, match="total_input_tokens"):
            MeetingMinutes(
                meeting_id="m-1",
                protocol_type=MeetingProtocolType.ROUND_ROBIN,
                leader_id="leader",
                participant_ids=("agent-a",),
                agenda=MeetingAgenda(title="Test"),
                contributions=(contrib,),
                total_input_tokens=999,
                total_output_tokens=20,
                started_at=_NOW,
                ended_at=_LATER,
            )

    def test_mismatched_output_tokens_rejected(self) -> None:
        """total_output_tokens != sum of contributions raises."""
        contrib = MeetingContribution(
            agent_id="agent-a",
            content="Input",
            phase=MeetingPhase.ROUND_ROBIN_TURN,
            turn_number=0,
            input_tokens=10,
            output_tokens=20,
            timestamp=_NOW,
        )
        with pytest.raises(ValidationError, match="total_output_tokens"):
            MeetingMinutes(
                meeting_id="m-1",
                protocol_type=MeetingProtocolType.ROUND_ROBIN,
                leader_id="leader",
                participant_ids=("agent-a",),
                agenda=MeetingAgenda(title="Test"),
                contributions=(contrib,),
                total_input_tokens=10,
                total_output_tokens=999,
                started_at=_NOW,
                ended_at=_LATER,
            )

    def test_empty_contributions_non_zero_totals_rejected(self) -> None:
        """Empty contributions with non-zero totals raises."""
        with pytest.raises(
            ValidationError,
            match="must be 0 when contributions are empty",
        ):
            MeetingMinutes(
                meeting_id="m-1",
                protocol_type=MeetingProtocolType.ROUND_ROBIN,
                leader_id="leader",
                participant_ids=("agent-a",),
                agenda=MeetingAgenda(title="Test"),
                contributions=(),
                total_input_tokens=100,
                total_output_tokens=0,
                started_at=_NOW,
                ended_at=_LATER,
            )
