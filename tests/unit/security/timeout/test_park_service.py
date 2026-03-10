"""Tests for the ParkService (park/resume agent contexts)."""

import json
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from ai_company.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from ai_company.core.enums import SeniorityLevel
from ai_company.engine.context import AgentContext
from ai_company.security.timeout.park_service import ParkService

pytestmark = pytest.mark.timeout(30)


def _make_agent_context() -> AgentContext:
    """Create a minimal AgentContext for testing."""
    identity = AgentIdentity(
        name="test-agent",
        role="developer",
        department="engineering",
        level=SeniorityLevel.MID,
        personality=PersonalityConfig(),
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )
    return AgentContext(
        execution_id=str(uuid4()),
        identity=identity,
        turn_count=1,
        started_at=datetime.now(UTC),
    )


@pytest.mark.unit
class TestParkService:
    """Tests for ParkService park/resume round-trip."""

    def test_park_creates_parked_context(self) -> None:
        """Parks an AgentContext and verifies ParkedContext fields."""
        context = _make_agent_context()
        service = ParkService()

        parked = service.park(
            context=context,
            approval_id="approval-1",
            agent_id="agent-1",
            task_id="task-1",
        )

        assert parked.agent_id == "agent-1"
        assert parked.approval_id == "approval-1"
        assert parked.task_id == "task-1"
        assert parked.execution_id == context.execution_id
        assert parked.id  # non-empty UUID default

    def test_park_serializes_context_json(self) -> None:
        """Verifies context_json is valid JSON."""
        context = _make_agent_context()
        service = ParkService()

        parked = service.park(
            context=context,
            approval_id="approval-1",
            agent_id="agent-1",
            task_id="task-1",
        )

        assert parked.context_json  # non-empty
        parsed = json.loads(parked.context_json)
        assert isinstance(parsed, dict)
        assert "execution_id" in parsed

    def test_resume_restores_context(self) -> None:
        """Parks then resumes, verifies round-trip fidelity."""
        context = _make_agent_context()
        service = ParkService()

        parked = service.park(
            context=context,
            approval_id="approval-1",
            agent_id="agent-1",
            task_id="task-1",
        )

        restored = service.resume(parked)

        assert restored.execution_id == context.execution_id
        assert restored.turn_count == context.turn_count
        assert restored.identity.name == context.identity.name
        assert restored.identity.role == context.identity.role
