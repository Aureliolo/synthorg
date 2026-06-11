"""Tests for the ParkService (park/resume agent contexts)."""

import json
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.engine.context import AgentContext
from synthorg.engine.park_service import ParkService
from synthorg.execution.parked_context import ParkedContext
from synthorg.hr.seniority import SeniorityLevel

pytestmark = pytest.mark.unit


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
        assert isinstance(parked.id, UUID)

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

    def test_resume_rejects_well_formed_json_that_is_not_a_context(self) -> None:
        """A ParkedContext whose JSON is not a valid AgentContext fails loud.

        ``context_json`` is valid JSON (so it passes ParkedContext's own
        construction validator) but does not deserialise into an
        ``AgentContext``; resume must surface that as a ValueError rather
        than letting the underlying ValidationError escape unlogged.
        """
        service = ParkService()
        parked = ParkedContext(
            execution_id="exec-1",
            agent_id="agent-1",
            approval_id="approval-1",
            parked_at=datetime.now(UTC),
            context_json="{}",
        )

        with pytest.raises(ValueError, match="Failed to resume parked context"):
            service.resume(parked)
