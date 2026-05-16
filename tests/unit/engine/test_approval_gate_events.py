"""Tests for ApprovalGate event stream integration."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.communication.event_stream.interrupt import (
    Interrupt,
    InterruptStore,
    InterruptType,
)
from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.event_stream.types import AgUiEventType
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.persistence.parked_context_protocol import ParkedContextRepository
from synthorg.security.timeout.park_service import ParkService
from tests.unit.engine.approval_helpers import make_escalation as _make_escalation

pytestmark = pytest.mark.unit


class TestApprovalGateEventStream:
    async def test_park_publishes_approval_interrupt(self) -> None:
        park_service = MagicMock(spec=ParkService)
        parked = MagicMock()
        parked.id = "parked-001"
        park_service.park.return_value = parked

        hub = EventStreamHub()
        queue = await hub.subscribe("session-abc")
        interrupt_store = InterruptStore()

        gate = ApprovalGate(
            park_service=park_service,
            event_hub=hub,
            interrupt_store=interrupt_store,
        )

        context = MagicMock()
        escalation = _make_escalation(
            approval_id="approval-001",
            tool_call_id="tc-001",
            tool_name="deploy_service",
        )

        await gate.park_context(
            escalation=escalation,
            context=context,
            agent_id="agent-eng-001",
            task_id="task-123",
            session_id="session-abc",
        )

        event = queue.get_nowait()
        assert event.type == AgUiEventType.APPROVAL_INTERRUPT
        assert event.session_id == "session-abc"
        assert event.payload["tool_name"] == "deploy_service"
        assert event.payload["approval_id"] == "approval-001"

    async def test_park_creates_interrupt_in_store(self) -> None:
        park_service = MagicMock(spec=ParkService)
        parked = MagicMock()
        parked.id = "parked-001"
        park_service.park.return_value = parked

        hub = EventStreamHub()
        interrupt_store = InterruptStore()

        gate = ApprovalGate(
            park_service=park_service,
            event_hub=hub,
            interrupt_store=interrupt_store,
        )

        context = MagicMock()
        escalation = _make_escalation(tool_name="deploy_service")

        await gate.park_context(
            escalation=escalation,
            context=context,
            agent_id="agent-eng-001",
            task_id="task-123",
            session_id="session-abc",
        )

        pending = await interrupt_store.list_pending(session_id="session-abc")
        assert len(pending) == 1
        assert pending[0].type == InterruptType.TOOL_APPROVAL
        assert pending[0].tool_name == "deploy_service"

    async def test_park_without_hub_no_error(self) -> None:
        park_service = MagicMock(spec=ParkService)
        parked = MagicMock()
        parked.id = "parked-001"
        park_service.park.return_value = parked

        gate = ApprovalGate(park_service=park_service)
        context = MagicMock()

        result = await gate.park_context(
            escalation=_make_escalation(),
            context=context,
            agent_id="agent-eng-001",
        )
        assert result is parked

    async def test_resume_publishes_approval_resumed(self) -> None:
        park_service = MagicMock(spec=ParkService)
        parked = MagicMock()
        parked.id = "parked-001"
        parked.agent_id = "agent-eng-001"
        parked.approval_id = "approval-001"
        parked.metadata = {}
        park_service.resume.return_value = MagicMock()

        repo = AsyncMock(spec=ParkedContextRepository)
        repo.get_by_approval.return_value = parked
        repo.delete.return_value = True

        hub = EventStreamHub()
        queue = await hub.subscribe("session-abc")

        gate = ApprovalGate(
            park_service=park_service,
            parked_context_repo=repo,
            event_hub=hub,
        )

        result = await gate.resume_context(
            "approval-001",
            session_id="session-abc",
        )
        assert result is not None

        event = queue.get_nowait()
        assert event.type == AgUiEventType.APPROVAL_RESUMED
        assert event.payload["approval_id"] == "approval-001"

    async def test_resume_resolves_interrupt(self) -> None:
        park_service = MagicMock(spec=ParkService)
        parked = MagicMock()
        parked.id = "parked-001"
        parked.agent_id = "agent-eng-001"
        parked.approval_id = "approval-001"
        parked.metadata = {"interrupt_id": "int-resume-001"}
        park_service.resume.return_value = MagicMock()

        repo = AsyncMock(spec=ParkedContextRepository)
        repo.get_by_approval.return_value = parked
        repo.delete.return_value = True

        store = InterruptStore()
        interrupt = Interrupt(
            id="int-resume-001",
            type=InterruptType.TOOL_APPROVAL,
            session_id="session-abc",
            agent_id="agent-eng-001",
            created_at=datetime(2026, 4, 13, tzinfo=UTC),
            timeout_seconds=300.0,
            tool_name="deploy",
        )
        await store.save(interrupt)
        assert len(await store.list_pending()) == 1

        gate = ApprovalGate(
            park_service=park_service,
            parked_context_repo=repo,
            interrupt_store=store,
        )

        await gate.resume_context(
            "approval-001",
            session_id="session-abc",
        )

        pending = await store.list_pending(session_id="session-abc")
        assert len(pending) == 0
