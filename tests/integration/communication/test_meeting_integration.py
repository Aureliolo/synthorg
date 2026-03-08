"""Integration tests for the meeting protocol system.

Tests full meeting lifecycle with protocol switching, budget
enforcement, and orchestrator coordination.
"""

import pytest

from ai_company.communication.meeting.config import (
    MeetingProtocolConfig,
    PositionPapersConfig,
    RoundRobinConfig,
    StructuredPhasesConfig,
)
from ai_company.communication.meeting.enums import (
    MeetingProtocolType,
    MeetingStatus,
)
from ai_company.communication.meeting.models import (
    AgentResponse,
    MeetingAgenda,
    MeetingAgendaItem,
)
from ai_company.communication.meeting.orchestrator import MeetingOrchestrator
from ai_company.communication.meeting.position_papers import (
    PositionPapersProtocol,
)
from ai_company.communication.meeting.protocol import (
    AgentCaller,  # noqa: TC001
    MeetingProtocol,  # noqa: TC001
)
from ai_company.communication.meeting.round_robin import RoundRobinProtocol
from ai_company.communication.meeting.structured_phases import (
    StructuredPhasesProtocol,
)

pytestmark = pytest.mark.timeout(30)


def _make_agent_caller(
    responses: dict[str, list[str]] | None = None,
) -> AgentCaller:
    """Create a deterministic mock agent caller."""
    call_counts: dict[str, int] = {}
    _responses = responses or {}

    async def _caller(
        agent_id: str,
        prompt: str,
        max_tokens: int,
    ) -> AgentResponse:
        call_counts.setdefault(agent_id, 0)
        idx = call_counts[agent_id]
        call_counts[agent_id] += 1

        agent_responses = _responses.get(agent_id, [])
        content = (
            agent_responses[idx]
            if idx < len(agent_responses)
            else f"Response from {agent_id} (call {idx})"
        )

        return AgentResponse(
            agent_id=agent_id,
            content=content,
            input_tokens=10,
            output_tokens=20,
        )

    return _caller


def _make_full_orchestrator(
    protocol_config: MeetingProtocolConfig | None = None,
    agent_caller: AgentCaller | None = None,
    task_creator: object | None = None,
) -> MeetingOrchestrator:
    """Create an orchestrator with all protocols registered."""
    cfg = protocol_config or MeetingProtocolConfig()
    registry: dict[MeetingProtocolType, MeetingProtocol] = {
        MeetingProtocolType.ROUND_ROBIN: RoundRobinProtocol(
            config=cfg.round_robin,
        ),
        MeetingProtocolType.POSITION_PAPERS: PositionPapersProtocol(
            config=cfg.position_papers,
        ),
        MeetingProtocolType.STRUCTURED_PHASES: StructuredPhasesProtocol(
            config=cfg.structured_phases,
        ),
    }
    caller = agent_caller or _make_agent_caller()
    return MeetingOrchestrator(
        protocol_registry=registry,
        agent_caller=caller,
        task_creator=task_creator,  # type: ignore[arg-type]
    )


@pytest.fixture
def agenda() -> MeetingAgenda:
    """Integration test agenda."""
    return MeetingAgenda(
        title="Sprint 42 Planning",
        context="Mid-quarter review and sprint planning",
        items=(
            MeetingAgendaItem(
                title="API Redesign",
                description="Discuss REST vs GraphQL",
                presenter_id="agent-backend",
            ),
            MeetingAgendaItem(
                title="Test Coverage",
                description="Target 80% coverage",
            ),
        ),
    )


@pytest.mark.integration
class TestFullMeetingLifecycle:
    """Full meeting lifecycle integration tests."""

    async def test_round_robin_full_lifecycle(
        self,
        agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_full_orchestrator()
        config = MeetingProtocolConfig(
            protocol=MeetingProtocolType.ROUND_ROBIN,
            round_robin=RoundRobinConfig(max_turns_per_agent=1),
        )

        record = await orchestrator.run_meeting(
            meeting_type_name="sprint_planning",
            protocol_config=config,
            agenda=agenda,
            leader_id="leader",
            participant_ids=("agent-a", "agent-b"),
            token_budget=5000,
        )

        assert record.status == MeetingStatus.COMPLETED
        assert record.minutes is not None
        assert record.minutes.protocol_type == MeetingProtocolType.ROUND_ROBIN
        assert record.minutes.leader_id == "leader"
        assert len(record.minutes.contributions) > 0
        assert record.token_budget == 5000

    async def test_position_papers_full_lifecycle(
        self,
        agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_full_orchestrator()
        config = MeetingProtocolConfig(
            protocol=MeetingProtocolType.POSITION_PAPERS,
        )

        record = await orchestrator.run_meeting(
            meeting_type_name="design_review",
            protocol_config=config,
            agenda=agenda,
            leader_id="leader",
            participant_ids=("agent-a", "agent-b", "agent-c"),
            token_budget=5000,
        )

        assert record.status == MeetingStatus.COMPLETED
        assert record.minutes is not None
        assert record.minutes.protocol_type == MeetingProtocolType.POSITION_PAPERS
        # 3 papers + 1 synthesis
        assert len(record.minutes.contributions) == 4

    async def test_structured_phases_with_conflicts(
        self,
        agenda: MeetingAgenda,
    ) -> None:
        responses = {
            "leader": [
                "CONFLICTS: YES\nDisagreement on API approach.",
                "Final decisions after discussion.",
            ],
        }
        caller = _make_agent_caller(responses=responses)
        orchestrator = _make_full_orchestrator(agent_caller=caller)
        config = MeetingProtocolConfig(
            protocol=MeetingProtocolType.STRUCTURED_PHASES,
        )

        record = await orchestrator.run_meeting(
            meeting_type_name="architecture_review",
            protocol_config=config,
            agenda=agenda,
            leader_id="leader",
            participant_ids=("agent-a", "agent-b"),
            token_budget=5000,
        )

        assert record.status == MeetingStatus.COMPLETED
        assert record.minutes is not None
        assert record.minutes.conflicts_detected is True
        # Input + conflict check + discussion + synthesis
        assert len(record.minutes.contributions) >= 4

    async def test_structured_phases_no_conflicts(
        self,
        agenda: MeetingAgenda,
    ) -> None:
        responses = {
            "leader": [
                "CONFLICTS: NO\nEveryone agrees.",
                "Summary and decisions.",
            ],
        }
        caller = _make_agent_caller(responses=responses)
        orchestrator = _make_full_orchestrator(agent_caller=caller)
        config = MeetingProtocolConfig(
            protocol=MeetingProtocolType.STRUCTURED_PHASES,
            structured_phases=StructuredPhasesConfig(
                skip_discussion_if_no_conflicts=True,
            ),
        )

        record = await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=config,
            agenda=agenda,
            leader_id="leader",
            participant_ids=("agent-a", "agent-b"),
            token_budget=5000,
        )

        assert record.status == MeetingStatus.COMPLETED
        assert record.minutes is not None
        assert record.minutes.conflicts_detected is False


@pytest.mark.integration
class TestProtocolSwitching:
    """Tests for switching between protocols."""

    async def test_same_orchestrator_different_protocols(
        self,
        agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_full_orchestrator()

        # Run round-robin
        record1 = await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=MeetingProtocolConfig(
                protocol=MeetingProtocolType.ROUND_ROBIN,
            ),
            agenda=agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=5000,
        )

        # Run position papers
        record2 = await orchestrator.run_meeting(
            meeting_type_name="review",
            protocol_config=MeetingProtocolConfig(
                protocol=MeetingProtocolType.POSITION_PAPERS,
            ),
            agenda=agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=5000,
        )

        # Run structured phases
        record3 = await orchestrator.run_meeting(
            meeting_type_name="planning",
            protocol_config=MeetingProtocolConfig(
                protocol=MeetingProtocolType.STRUCTURED_PHASES,
            ),
            agenda=agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=5000,
        )

        assert record1.protocol_type == MeetingProtocolType.ROUND_ROBIN
        assert record2.protocol_type == MeetingProtocolType.POSITION_PAPERS
        assert record3.protocol_type == MeetingProtocolType.STRUCTURED_PHASES

        records = orchestrator.get_records()
        assert len(records) == 3


@pytest.mark.integration
class TestTokenBudgetEnforcement:
    """Tests for token budget enforcement across protocols."""

    async def test_round_robin_budget_enforcement(
        self,
        agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_full_orchestrator()
        config = MeetingProtocolConfig(
            protocol=MeetingProtocolType.ROUND_ROBIN,
            round_robin=RoundRobinConfig(
                max_turns_per_agent=100,
                max_total_turns=1000,
            ),
        )

        record = await orchestrator.run_meeting(
            meeting_type_name="long_meeting",
            protocol_config=config,
            agenda=agenda,
            leader_id="leader",
            participant_ids=("agent-a", "agent-b", "agent-c"),
            token_budget=100,
        )

        assert record.status == MeetingStatus.COMPLETED
        # Token usage should be bounded
        assert record.minutes is not None
        assert record.minutes.total_tokens <= 200  # Generous upper bound

    async def test_token_counts_accumulate(
        self,
        agenda: MeetingAgenda,
    ) -> None:
        orchestrator = _make_full_orchestrator()
        config = MeetingProtocolConfig(
            protocol=MeetingProtocolType.ROUND_ROBIN,
            round_robin=RoundRobinConfig(max_turns_per_agent=1),
        )

        record = await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=config,
            agenda=agenda,
            leader_id="leader",
            participant_ids=("agent-a", "agent-b"),
            token_budget=10000,
        )

        assert record.minutes is not None
        assert record.minutes.total_input_tokens > 0
        assert record.minutes.total_output_tokens > 0
        assert (
            record.minutes.total_tokens
            == record.minutes.total_input_tokens + record.minutes.total_output_tokens
        )


@pytest.mark.integration
class TestErrorRecovery:
    """Tests for error handling in the meeting lifecycle."""

    async def test_agent_failure_produces_failed_record(
        self,
        agenda: MeetingAgenda,
    ) -> None:
        async def _failing_caller(
            agent_id: str,
            prompt: str,
            max_tokens: int,
        ) -> AgentResponse:
            msg = "Connection refused"
            raise RuntimeError(msg)

        orchestrator = _make_full_orchestrator(
            agent_caller=_failing_caller,
        )

        record = await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=MeetingProtocolConfig(),
            agenda=agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=5000,
        )

        assert record.status == MeetingStatus.FAILED
        assert record.error_message is not None
        assert "Connection refused" in record.error_message
        assert record.minutes is None

    async def test_failed_meeting_recorded_in_audit_trail(
        self,
        agenda: MeetingAgenda,
    ) -> None:
        async def _failing_caller(
            agent_id: str,
            prompt: str,
            max_tokens: int,
        ) -> AgentResponse:
            msg = "timeout"
            raise RuntimeError(msg)

        orchestrator = _make_full_orchestrator(
            agent_caller=_failing_caller,
        )

        await orchestrator.run_meeting(
            meeting_type_name="standup",
            protocol_config=MeetingProtocolConfig(),
            agenda=agenda,
            leader_id="leader",
            participant_ids=("agent-a",),
            token_budget=5000,
        )

        records = orchestrator.get_records()
        assert len(records) == 1
        assert records[0].status == MeetingStatus.FAILED


@pytest.mark.integration
class TestMeetingTypeConfigIntegration:
    """Tests for MeetingTypeConfig with protocol_config field."""

    def test_meeting_type_config_has_protocol_config(self) -> None:
        from ai_company.communication.config import MeetingTypeConfig

        config = MeetingTypeConfig(
            name="standup",
            frequency="daily",
            participants=("engineering",),
        )
        assert isinstance(config.protocol_config, MeetingProtocolConfig)
        assert config.protocol_config.protocol == MeetingProtocolType.ROUND_ROBIN

    def test_meeting_type_config_custom_protocol(self) -> None:
        from ai_company.communication.config import MeetingTypeConfig

        config = MeetingTypeConfig(
            name="design_review",
            trigger="pr_opened",
            participants=("engineering", "design"),
            protocol_config=MeetingProtocolConfig(
                protocol=MeetingProtocolType.POSITION_PAPERS,
                position_papers=PositionPapersConfig(
                    max_tokens_per_position=500,
                ),
            ),
        )
        assert config.protocol_config.protocol == MeetingProtocolType.POSITION_PAPERS
        assert config.protocol_config.position_papers.max_tokens_per_position == 500


@pytest.mark.integration
class TestCommunicationReExports:
    """Tests that meeting types are accessible from the communication package."""

    def test_meeting_types_importable(self) -> None:
        from ai_company.communication import (
            MeetingError,
            MeetingProtocolType,
        )

        # Verify they are the correct types
        assert MeetingProtocolType.ROUND_ROBIN.value == "round_robin"
        assert issubclass(MeetingError, Exception)

    def test_message_type_meeting_contribution(self) -> None:
        from ai_company.communication import MessageType

        assert MessageType.MEETING_CONTRIBUTION.value == "meeting_contribution"
