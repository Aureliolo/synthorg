"""Tests for position-papers meeting protocol."""

import pytest

from synthorg.communication.meeting.config import PositionPapersConfig
from synthorg.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
)
from synthorg.communication.meeting.errors import (
    MeetingBudgetExhaustedError,
)
from synthorg.communication.meeting.models import MeetingAgenda
from synthorg.communication.meeting.position_papers import (
    PositionPapersProtocol,
)
from synthorg.communication.meeting.protocol import MeetingProtocol
from tests.unit.communication.meeting.conftest import (
    make_mock_agent_caller,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestPositionPapersProtocolType:
    """Tests for protocol type identification."""

    def test_get_protocol_type(self) -> None:
        protocol = PositionPapersProtocol(config=PositionPapersConfig())
        assert protocol.get_protocol_type() == MeetingProtocolType.POSITION_PAPERS

    def test_conforms_to_protocol(self) -> None:
        protocol = PositionPapersProtocol(config=PositionPapersConfig())
        assert isinstance(protocol, MeetingProtocol)


@pytest.mark.unit
class TestPositionPapersExecution:
    """Tests for position-papers protocol execution."""

    async def test_basic_execution(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        protocol = PositionPapersProtocol(config=PositionPapersConfig())

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        assert minutes.meeting_id == meeting_id
        assert minutes.protocol_type == MeetingProtocolType.POSITION_PAPERS
        assert minutes.leader_id == leader_id

    async def test_contributions_structure(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        protocol = PositionPapersProtocol(config=PositionPapersConfig())

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        # 3 position papers + 1 synthesis = 4 contributions
        assert len(minutes.contributions) == 4

        # Position papers
        papers = [
            c for c in minutes.contributions if c.phase == MeetingPhase.POSITION_PAPER
        ]
        assert len(papers) == 3

        # Synthesis
        synthesis = [
            c for c in minutes.contributions if c.phase == MeetingPhase.SYNTHESIS
        ]
        assert len(synthesis) == 1

    async def test_parallel_execution_all_participants(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        protocol = PositionPapersProtocol(config=PositionPapersConfig())

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        paper_agents = {
            c.agent_id
            for c in minutes.contributions
            if c.phase == MeetingPhase.POSITION_PAPER
        }
        assert paper_agents == set(participant_ids)

    async def test_synthesizer_is_leader_by_default(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        protocol = PositionPapersProtocol(config=PositionPapersConfig())

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        synthesis = [
            c for c in minutes.contributions if c.phase == MeetingPhase.SYNTHESIS
        ]
        assert synthesis[0].agent_id == leader_id

    async def test_custom_synthesizer(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        config = PositionPapersConfig(synthesizer="agent-cto")
        protocol = PositionPapersProtocol(config=config)

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=("agent-a", "agent-b"),
            agent_caller=caller,
            token_budget=10000,
        )

        synthesis = [
            c for c in minutes.contributions if c.phase == MeetingPhase.SYNTHESIS
        ]
        assert synthesis[0].agent_id == "agent-cto"

    async def test_summary_is_synthesis_content(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        responses = {
            "leader-agent": ["Synthesis: agreed on REST API"],
        }
        caller = make_mock_agent_caller(responses=responses)
        protocol = PositionPapersProtocol(config=PositionPapersConfig())

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=("agent-a",),
            agent_caller=caller,
            token_budget=10000,
        )

        assert minutes.summary == "Synthesis: agreed on REST API"

    async def test_token_tracking(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller(input_tokens=15, output_tokens=25)
        protocol = PositionPapersProtocol(config=PositionPapersConfig())

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=("agent-a", "agent-b"),
            agent_caller=caller,
            token_budget=10000,
        )

        # 2 papers + 1 synthesis = 3 calls, each 15+25=40 tokens
        assert minutes.total_input_tokens == 45
        assert minutes.total_output_tokens == 75
        assert minutes.total_tokens == 120

    async def test_timing_fields(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        protocol = PositionPapersProtocol(config=PositionPapersConfig())

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        assert minutes.started_at <= minutes.ended_at

    async def test_single_participant(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        protocol = PositionPapersProtocol(config=PositionPapersConfig())

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=("agent-a",),
            agent_caller=caller,
            token_budget=10000,
        )

        # 1 paper + 1 synthesis = 2
        assert len(minutes.contributions) == 2

    async def test_budget_exhaustion_before_synthesis(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        """Tight budget triggers MeetingBudgetExhaustedError before synthesis."""
        caller = make_mock_agent_caller(
            input_tokens=30,
            output_tokens=30,
        )
        protocol = PositionPapersProtocol(config=PositionPapersConfig())

        with pytest.raises(MeetingBudgetExhaustedError):
            await protocol.run(
                meeting_id=meeting_id,
                agenda=simple_agenda,
                leader_id=leader_id,
                participant_ids=("agent-a", "agent-b", "agent-c"),
                agent_caller=caller,
                token_budget=60,  # 3 papers x 60 tokens = 180, exceeds 60
            )
