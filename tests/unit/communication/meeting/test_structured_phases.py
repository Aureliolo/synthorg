"""Tests for structured-phases meeting protocol."""

import pytest

from synthorg.communication.meeting.config import StructuredPhasesConfig
from synthorg.communication.meeting.enums import (
    MeetingPhase,
    MeetingProtocolType,
)
from synthorg.communication.meeting.errors import (
    MeetingBudgetExhaustedError,
)
from synthorg.communication.meeting.models import MeetingAgenda
from synthorg.communication.meeting.protocol import ConflictDetector, MeetingProtocol
from synthorg.communication.meeting.structured_phases import (
    KeywordConflictDetector,
    StructuredPhasesProtocol,
)
from tests.unit.communication.meeting.conftest import (
    make_mock_agent_caller,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestStructuredPhasesProtocolType:
    """Tests for protocol type identification."""

    def test_get_protocol_type(self) -> None:
        protocol = StructuredPhasesProtocol(
            config=StructuredPhasesConfig(),
        )
        assert protocol.get_protocol_type() == MeetingProtocolType.STRUCTURED_PHASES

    def test_conforms_to_protocol(self) -> None:
        protocol = StructuredPhasesProtocol(
            config=StructuredPhasesConfig(),
        )
        assert isinstance(protocol, MeetingProtocol)


@pytest.mark.unit
class TestStructuredPhasesExecution:
    """Tests for structured-phases protocol execution."""

    async def test_basic_execution(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        protocol = StructuredPhasesProtocol(
            config=StructuredPhasesConfig(),
        )

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        assert minutes.meeting_id == meeting_id
        assert minutes.protocol_type == MeetingProtocolType.STRUCTURED_PHASES
        assert minutes.leader_id == leader_id

    async def test_input_gathering_parallel(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        protocol = StructuredPhasesProtocol(
            config=StructuredPhasesConfig(),
        )

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        input_contribs = [
            c for c in minutes.contributions if c.phase == MeetingPhase.INPUT_GATHERING
        ]
        assert len(input_contribs) == len(participant_ids)

        input_agents = {c.agent_id for c in input_contribs}
        assert input_agents == set(participant_ids)

    async def test_no_conflicts_skips_discussion(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        # Leader response says no conflicts
        responses = {
            "leader-agent": [
                "CONFLICTS: NO\nAll participants agree.",
                "Final synthesis with decisions.",
            ],
        }
        caller = make_mock_agent_caller(responses=responses)
        config = StructuredPhasesConfig(
            skip_discussion_if_no_conflicts=True,
        )
        protocol = StructuredPhasesProtocol(config=config)

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        assert minutes.conflicts_detected is False
        # No discussion contributions from participants
        discussion_contribs = [
            c
            for c in minutes.contributions
            if c.phase == MeetingPhase.DISCUSSION and c.agent_id != leader_id
        ]
        assert len(discussion_contribs) == 0

    async def test_conflicts_trigger_discussion(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        responses = {
            "leader-agent": [
                "CONFLICTS: YES\nDisagreement on API style.",
                "Final synthesis after discussion.",
            ],
        }
        caller = make_mock_agent_caller(responses=responses)
        config = StructuredPhasesConfig(
            skip_discussion_if_no_conflicts=True,
        )
        protocol = StructuredPhasesProtocol(config=config)

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        assert minutes.conflicts_detected is True
        discussion_contribs = [
            c
            for c in minutes.contributions
            if c.phase == MeetingPhase.DISCUSSION and c.agent_id != leader_id
        ]
        assert len(discussion_contribs) == len(participant_ids)

    async def test_skip_discussion_disabled_always_discusses(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        responses = {
            "leader-agent": [
                "CONFLICTS: NO\nAll agree.",
                "Synthesis.",
            ],
        }
        caller = make_mock_agent_caller(responses=responses)
        config = StructuredPhasesConfig(
            skip_discussion_if_no_conflicts=False,
        )
        protocol = StructuredPhasesProtocol(config=config)
        participants = ("agent-a", "agent-b")

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participants,
            agent_caller=caller,
            token_budget=10000,
        )

        # Even without conflicts, discussion happens
        discussion_contribs = [
            c
            for c in minutes.contributions
            if c.phase == MeetingPhase.DISCUSSION and c.agent_id != leader_id
        ]
        assert len(discussion_contribs) == 2

    async def test_synthesis_phase(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        responses = {
            "leader-agent": [
                "CONFLICTS: NO",
                "Decisions: 1. Use REST. Action items: - Deploy API",
            ],
        }
        caller = make_mock_agent_caller(responses=responses)
        protocol = StructuredPhasesProtocol(
            config=StructuredPhasesConfig(),
        )

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
        assert len(synthesis) == 1
        assert synthesis[0].agent_id == leader_id
        assert minutes.summary == synthesis[0].content

    async def test_token_tracking(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        responses = {
            "leader-agent": ["CONFLICTS: NO", "Synthesis"],
        }
        caller = make_mock_agent_caller(
            responses=responses,
            input_tokens=10,
            output_tokens=20,
        )
        config = StructuredPhasesConfig(
            skip_discussion_if_no_conflicts=True,
        )
        protocol = StructuredPhasesProtocol(config=config)

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=("agent-a",),
            agent_caller=caller,
            token_budget=10000,
        )

        # 1 input + 1 conflict check + 1 synthesis = 3 calls
        assert minutes.total_input_tokens == 30
        assert minutes.total_output_tokens == 60
        assert minutes.total_tokens == 90

    async def test_timing_fields(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        participant_ids: tuple[str, ...],
        meeting_id: str,
    ) -> None:
        caller = make_mock_agent_caller()
        protocol = StructuredPhasesProtocol(
            config=StructuredPhasesConfig(),
        )

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=participant_ids,
            agent_caller=caller,
            token_budget=10000,
        )

        assert minutes.started_at <= minutes.ended_at

    async def test_budget_exhaustion_raises_on_synthesis(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        # Very tight budget — will exhaust before synthesis
        caller = make_mock_agent_caller(
            input_tokens=30,
            output_tokens=30,
        )
        config = StructuredPhasesConfig(
            skip_discussion_if_no_conflicts=True,
        )
        protocol = StructuredPhasesProtocol(config=config)

        with pytest.raises(MeetingBudgetExhaustedError):
            await protocol.run(
                meeting_id=meeting_id,
                agenda=simple_agenda,
                leader_id=leader_id,
                participant_ids=("agent-a", "agent-b", "agent-c"),
                agent_caller=caller,
                token_budget=60,  # Very tight: 3 inputs = 180 tokens
            )

    async def test_single_participant(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        responses = {
            "leader-agent": ["CONFLICTS: NO", "Summary"],
        }
        caller = make_mock_agent_caller(responses=responses)
        protocol = StructuredPhasesProtocol(
            config=StructuredPhasesConfig(),
        )

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=("agent-a",),
            agent_caller=caller,
            token_budget=10000,
        )

        # 1 input + 1 conflict check + 1 synthesis = 3 contributions
        assert len(minutes.contributions) == 3


@pytest.mark.unit
class TestStructuredPhasesConflictDetector:
    """Tests for pluggable conflict detector."""

    def test_keyword_detector_conforms_to_protocol(self) -> None:
        detector = KeywordConflictDetector()
        assert isinstance(detector, ConflictDetector)

    def test_keyword_detector_detects_yes(self) -> None:
        detector = KeywordConflictDetector()
        assert detector.detect("CONFLICTS: YES\nSome disagreement") is True

    def test_keyword_detector_detects_no(self) -> None:
        detector = KeywordConflictDetector()
        assert detector.detect("CONFLICTS: NO\nAll agree") is False

    def test_keyword_detector_case_insensitive(self) -> None:
        detector = KeywordConflictDetector()
        assert detector.detect("conflicts: yes") is True
        assert detector.detect("Conflicts: Yes") is True

    async def test_custom_conflict_detector(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        """Custom detector overrides default keyword matching."""

        class _AlwaysConflict:
            def detect(self, response_content: str) -> bool:
                return True

        responses = {
            "leader-agent": [
                "CONFLICTS: NO\nAll agree.",
                "Synthesis after discussion.",
            ],
        }
        caller = make_mock_agent_caller(responses=responses)
        config = StructuredPhasesConfig(
            skip_discussion_if_no_conflicts=True,
        )
        protocol = StructuredPhasesProtocol(
            config=config,
            conflict_detector=_AlwaysConflict(),
        )

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=("agent-a",),
            agent_caller=caller,
            token_budget=10000,
        )

        # Custom detector returns True, so discussion happens despite
        # leader saying "CONFLICTS: NO"
        assert minutes.conflicts_detected is True
        discussion_contribs = [
            c
            for c in minutes.contributions
            if c.phase == MeetingPhase.DISCUSSION and c.agent_id != leader_id
        ]
        assert len(discussion_contribs) == 1

    async def test_never_conflict_detector_skips_discussion(
        self,
        simple_agenda: MeetingAgenda,
        leader_id: str,
        meeting_id: str,
    ) -> None:
        """Custom detector that never detects conflicts skips discussion."""

        class _NeverConflict:
            def detect(self, response_content: str) -> bool:
                return False

        responses = {
            "leader-agent": [
                "CONFLICTS: YES\nBig disagreement!",
                "Synthesis.",
            ],
        }
        caller = make_mock_agent_caller(responses=responses)
        config = StructuredPhasesConfig(
            skip_discussion_if_no_conflicts=True,
        )
        protocol = StructuredPhasesProtocol(
            config=config,
            conflict_detector=_NeverConflict(),
        )

        minutes = await protocol.run(
            meeting_id=meeting_id,
            agenda=simple_agenda,
            leader_id=leader_id,
            participant_ids=("agent-a",),
            agent_caller=caller,
            token_budget=10000,
        )

        # Custom detector returns False, so discussion is skipped despite
        # leader saying "CONFLICTS: YES"
        assert minutes.conflicts_detected is False
        discussion_contribs = [
            c
            for c in minutes.contributions
            if c.phase == MeetingPhase.DISCUSSION and c.agent_id != leader_id
        ]
        assert len(discussion_contribs) == 0
