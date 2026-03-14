"""Test fixtures and factories for meeting protocol tests."""

import pytest

from synthorg.communication.meeting.config import (
    MeetingProtocolConfig,
    PositionPapersConfig,
    RoundRobinConfig,
    StructuredPhasesConfig,
)
from synthorg.communication.meeting.models import (
    AgentResponse,
    MeetingAgenda,
    MeetingAgendaItem,
)
from synthorg.communication.meeting.protocol import AgentCaller


def make_agent_response(
    agent_id: str,
    content: str = "Mock response",
    input_tokens: int = 10,
    output_tokens: int = 20,
    cost_usd: float = 0.001,
) -> AgentResponse:
    """Create an AgentResponse for testing."""
    return AgentResponse(
        agent_id=agent_id,
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


def make_mock_agent_caller(
    responses: dict[str, list[str]] | None = None,
    default_content: str = "Mock response",
    input_tokens: int = 10,
    output_tokens: int = 20,
) -> AgentCaller:
    """Create a mock AgentCaller that returns predetermined responses.

    Args:
        responses: Mapping of agent_id to list of response contents.
            Each call pops the next response from the list.
        default_content: Fallback content when no specific response is
            configured for an agent.
        input_tokens: Token count for each response.
        output_tokens: Token count for each response.

    Returns:
        An async callable matching the AgentCaller signature.
    """
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
            agent_responses[idx] if idx < len(agent_responses) else default_content
        )

        return AgentResponse(
            agent_id=agent_id,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    return _caller


@pytest.fixture
def simple_agenda() -> MeetingAgenda:
    """A simple meeting agenda for testing."""
    return MeetingAgenda(
        title="Sprint Planning",
        context="Sprint 42 planning session",
        items=(
            MeetingAgendaItem(
                title="API Design",
                description="Discuss REST API structure",
            ),
            MeetingAgendaItem(
                title="Testing Strategy",
                description="Agree on test coverage targets",
            ),
        ),
    )


@pytest.fixture
def leader_id() -> str:
    """Default leader ID for tests."""
    return "leader-agent"


@pytest.fixture
def participant_ids() -> tuple[str, ...]:
    """Default participant IDs for tests."""
    return ("agent-a", "agent-b", "agent-c")


@pytest.fixture
def meeting_id() -> str:
    """Default meeting ID for tests."""
    return "meeting-001"


@pytest.fixture
def default_round_robin_config() -> RoundRobinConfig:
    """Default round-robin configuration."""
    return RoundRobinConfig()


@pytest.fixture
def default_position_papers_config() -> PositionPapersConfig:
    """Default position papers configuration."""
    return PositionPapersConfig()


@pytest.fixture
def default_structured_phases_config() -> StructuredPhasesConfig:
    """Default structured phases configuration."""
    return StructuredPhasesConfig()


@pytest.fixture
def default_protocol_config() -> MeetingProtocolConfig:
    """Default meeting protocol configuration."""
    return MeetingProtocolConfig()
