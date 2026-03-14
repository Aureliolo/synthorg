"""Tests for round-robin meeting protocol."""

import pytest

from synthorg.communication.meeting.config import RoundRobinConfig
from synthorg.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
)
from synthorg.communication.meeting.errors import (
    MeetingBudgetExhaustedError,
)
from synthorg.communication.meeting.models import MeetingAgenda
from synthorg.communication.meeting.protocol import MeetingProtocol
from synthorg.communication.meeting.round_robin import RoundRobinProtocol
from tests.unit.communication.meeting.conftest import (
    make_mock_agent_caller,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestRoundRobinProtocolType:
    """Tests for protocol type identification."""

    def test_get_protocol_type(self) -> None:
        protocol = RoundRobinProtocol(config=RoundRobinConfig())
        assert protocol.get_protocol_type() == MeetingProtocolType.ROUND_ROBIN

    def test_conforms_to_protocol(self) -> None:
        protocol = RoundRobinProtocol(config=RoundRobinConfig())
        assert isinstance(protocol, MeetingProtocol)


@pytest.mark.unit
class TestRoundRobinExecution:
    """Tests for round-robin protocol execution."""

    async def test_basic_execution(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        protocol = RoundRobinProtocol(config=RoundRobinConfig())

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        assert minutes.meeting_id == meeting_id
        assert minutes.protocol_type == MeetingProtocolType.ROUND_ROBIN
        assert minutes.leader_id == leader_id
        assert minutes.participant_ids == participant_ids

    async def test_contributions_recorded(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        config = RoundRobinConfig(max_turns_per_agent=1)
        protocol = RoundRobinProtocol(config=config)

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        # 3 participants x 1 turn + 1 summary = 4 contributions
        assert len(minutes.contributions) == 4
        # First 3 are round-robin turns
        for contrib in minutes.contributions[:3]:
            assert contrib.phase == MeetingPhase.ROUND_ROBIN_TURN
        # Last is summary
        assert minutes.contributions[3].phase == MeetingPhase.SUMMARY

    async def test_turn_numbers_sequential(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        config = RoundRobinConfig(max_turns_per_agent=1)
        protocol = RoundRobinProtocol(config=config)

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        turn_numbers = [c.turn_number for c in minutes.contributions]
        assert turn_numbers == [0, 1, 2, 3]

    async def test_max_total_turns_limits_contributions(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        config = RoundRobinConfig(
            max_turns_per_agent=10,
            max_total_turns=2,
        )
        protocol = RoundRobinProtocol(config=config)
        participants = ("agent-a", "agent-b", "agent-c")

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participants,
            agent_caller=caller,
            token_budget=10000,
        )

        # 2 turns + 1 summary
        round_robin_contribs = [
            c for c in minutes.contributions if c.phase == MeetingPhase.ROUND_ROBIN_TURN
        ]
        assert len(round_robin_contribs) == 2

    async def test_multiple_rounds(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        config = RoundRobinConfig(
            max_turns_per_agent=2,
            max_total_turns=100,
        )
        protocol = RoundRobinProtocol(config=config)
        participants = ("agent-a", "agent-b")

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participants,
            agent_caller=caller,
            token_budget=10000,
        )

        # 2 participants x 2 rounds + 1 summary = 5
        assert len(minutes.contributions) == 5

    async def test_no_summary_when_disabled(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        config = RoundRobinConfig(
            max_turns_per_agent=1,
            leader_summarizes=False,
        )
        protocol = RoundRobinProtocol(config=config)

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        # 3 participants x 1 turn, no summary
        assert len(minutes.contributions) == 3
        assert minutes.summary == ""

    async def test_token_tracking(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller(input_tokens=15, output_tokens=25)
        config = RoundRobinConfig(max_turns_per_agent=1)
        protocol = RoundRobinProtocol(config=config)
        participants = ("agent-a",)

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participants,
            agent_caller=caller,
            token_budget=10000,
        )

        # 1 turn + 1 summary = 2 calls, each 15+25=40 tokens
        assert minutes.total_input_tokens == 30
        assert minutes.total_output_tokens == 50
        assert minutes.total_tokens == 80

    async def test_budget_exhaustion_stops_turns(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        # Each call uses 30 tokens, budget is 50 (20% reserve = 40 discussion).
        # agent-a: 30 used (< 40), agent-b: 60 used (>= 40, stops after).
        # Budget check is pre-turn, so the call that crosses is completed.
        # With leader_summarizes=True (default), budget exhaustion raises
        # MeetingBudgetExhaustedError when summary cannot be generated.
        caller = make_mock_agent_caller(input_tokens=10, output_tokens=20)
        config = RoundRobinConfig(
            max_turns_per_agent=5,
            max_total_turns=100,
        )
        protocol = RoundRobinProtocol(config=config)
        participants = ("agent-a", "agent-b", "agent-c")

        with pytest.raises(MeetingBudgetExhaustedError, match="budget exhausted"):
            await protocol.run(
                meeting_id=meeting_id,
                agenda=simple_agenda,
                leader_id=leader_id,
                participant_ids=participants,
                agent_caller=caller,
                token_budget=50,
            )

    async def test_budget_exhaustion_no_summary_returns_minutes(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        """When leader_summarizes is disabled, budget exhaustion returns minutes."""
        caller = make_mock_agent_caller(input_tokens=10, output_tokens=20)
        config = RoundRobinConfig(
            max_turns_per_agent=5,
            max_total_turns=100,
            leader_summarizes=False,
        )
        protocol = RoundRobinProtocol(config=config)
        participants = ("agent-a", "agent-b", "agent-c")

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participants,
            agent_caller=caller,
            token_budget=50,
        )

        # Budget stops before agent-c; 2 turns completed, no summary
        round_robin_contribs = [
            c for c in minutes.contributions if c.phase == MeetingPhase.ROUND_ROBIN_TURN
        ]
        max_turns = len(participants) * config.max_turns_per_agent
        assert len(round_robin_contribs) < max_turns
        assert minutes.summary == ""

    async def test_timing_fields(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        protocol = RoundRobinProtocol(config=RoundRobinConfig())

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        assert minutes.started_at <= minutes.ended_at

    async def test_custom_responses(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        responses = {
            "agent-a": ["I think we should use REST"],
            "leader-agent": ["Summary: REST API agreed"],
        }
        caller = make_mock_agent_caller(responses=responses)
        config = RoundRobinConfig(max_turns_per_agent=1)
        protocol = RoundRobinProtocol(config=config)

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=("agent-a",),
            agent_caller=caller,
            token_budget=10000,
        )

        assert minutes.contributions[0].content == "I think we should use REST"
        assert minutes.summary == "Summary: REST API agreed"
