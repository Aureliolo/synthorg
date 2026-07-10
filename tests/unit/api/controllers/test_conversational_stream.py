# module-kind: tests
"""Unit tests for the conversational SSE ``error`` frame builder.

Once the ``text/event-stream`` headers are on the wire the RFC 9457 handler
can no longer run, so a post-start failure surfaces only as an in-stream
``event: error`` frame. These tests pin the frame's redaction contract: a
typed :class:`DomainError` crosses the wire with its client-safe detail /
code / retry semantics (scrubbed to the fallback for a 5xx), while an
unexpected fault stays opaque so its message cannot leak.
"""

import json as _json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers._conversational_stream import (
    _error_frame,
    chat_answer_stream,
)
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.domain_errors import (
    NotFoundError,
    PerOperationRateLimitError,
    ServiceUnavailableError,
)
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
from synthorg.meta.chief_of_staff.models import (
    ChatAnswerComplete,
    ChatAnswerDelta,
    ChatQuery,
    CitedRecord,
)
from synthorg.meta.models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgTelemetrySummary,
)
from synthorg.meta.signal_models import OrgSignalSnapshot
from synthorg.meta.signals.service import SignalsService
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.persistence.task_protocol import TaskRepository
from tests._shared import FakeClock, make_app_state, mock_of, sid

_NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=UTC)

pytestmark = pytest.mark.unit


def _payload(exc: Exception) -> dict[str, object]:
    """Build the error frame for *exc* and return its parsed data body.

    Returns:
        The JSON-decoded ``data`` payload of the ``error`` frame.
    """
    frame = _error_frame(exc)
    assert frame["event"] == "error"
    parsed = _json.loads(frame["data"])
    assert isinstance(parsed, dict)
    return parsed


class TestErrorFrame:
    """The SSE ``error`` frame's redaction + discrimination contract."""

    def test_non_domain_error_stays_opaque(self) -> None:
        payload = _payload(ValueError("connection string user:hunter2@db"))
        # Only the class name crosses the wire; the message never does.
        assert payload == {"error": "Internal error: ValueError"}
        assert "hunter2" not in str(payload["error"])

    def test_client_error_surfaces_detail_and_code(self) -> None:
        payload = _payload(NotFoundError("The requested agent is not registered"))
        assert payload["error"] == "The requested agent is not registered"
        assert payload["error_code"] == ErrorCode.RESOURCE_NOT_FOUND.value
        assert payload["retryable"] is False
        assert "retry_after" not in payload

    def test_server_error_scrubs_to_fallback(self) -> None:
        # A 503 carries a 5xx-safe fallback, never the constructed detail
        # (which could name an internal dependency).
        payload = _payload(
            ServiceUnavailableError("chat backend down: internal-host:5432")
        )
        assert payload["error"] == "Service unavailable"
        assert "5432" not in str(payload["error"])
        assert payload["error_code"] == ErrorCode.SERVICE_UNAVAILABLE.value
        assert payload["retryable"] is True

    def test_rate_limit_carries_retry_after(self) -> None:
        payload = _payload(PerOperationRateLimitError(retry_after=30))
        assert payload["retryable"] is True
        assert payload["retry_after"] == 30
        assert payload["error_code"] == ErrorCode.PER_OPERATION_RATE_LIMITED.value


def _snapshot() -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=7.5,
            avg_success_rate=0.85,
            avg_collaboration_score=6.0,
            agent_count=10,
        ),
        budget=OrgBudgetSummary(
            total_spend=150.0,
            productive_ratio=0.6,
            coordination_ratio=0.3,
            system_ratio=0.1,
            forecast_confidence=0.8,
            orchestration_overhead=0.5,
        ),
        coordination=OrgCoordinationSummary(),
        scaling=OrgScalingSummary(),
        errors=OrgErrorSummary(),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


def _in_progress_task() -> Task:
    return Task(
        title="Fix login",
        description="Fix the login flow",
        type=TaskType.DEVELOPMENT,
        project=sid("proj-platform"),
        created_by=sid("planner"),
        assigned_to=sid("agent-1"),
        status=TaskStatus.IN_PROGRESS,
    )


def _connected_backend() -> PersistenceBackend:
    backend: PersistenceBackend = mock_of[PersistenceBackend](
        is_connected=True,
        tasks=mock_of[TaskRepository](
            query=AsyncMock(side_effect=[(_in_progress_task(),), ()]),
            count=AsyncMock(side_effect=[1, 0]),
        ),
        projects=mock_of[ProjectRepository](
            query=AsyncMock(return_value=()),
            count=AsyncMock(return_value=0),
        ),
    )
    return backend


class TestChatAnswerStream:
    """The streaming controller threads org_state and serialises citations."""

    async def test_complete_frame_carries_org_state_citations(self) -> None:
        store = mock_of[ApprovalStoreProtocol](list_items=AsyncMock(return_value=()))
        state = make_app_state(
            approval_store=store, config_resolver=None, clock=FakeClock(start=_NOW)
        )
        state.wire(PersistenceStateSlice, backend=_connected_backend())

        complete = ChatAnswerComplete(
            answer="Working on the login fix.",
            sources=(NotBlankStr("tasks"),),
            cited_records=(
                CitedRecord(
                    kind="task",
                    record_id=sid("task-1"),
                    label="Fix login",
                    status="in_progress",
                ),
            ),
            confidence=0.9,
        )
        captured: dict[str, object] = {}

        async def _fake_ask_stream(
            query: ChatQuery,
            snapshot: OrgSignalSnapshot,
            *,
            org_state: object = None,
        ) -> AsyncIterator[ChatAnswerDelta | ChatAnswerComplete]:
            captured["org_state"] = org_state
            yield ChatAnswerDelta(delta="Working ")
            yield complete

        chat = mock_of[ChiefOfStaffChat](ask_stream=_fake_ask_stream)
        signals = mock_of[SignalsService](
            get_org_snapshot=AsyncMock(return_value=_snapshot())
        )

        frames = [
            frame
            async for frame in chat_answer_stream(
                app_state=state,
                chat_backend=chat,
                signals_service=signals,
                question=NotBlankStr("What is the org working on?"),
            )
        ]

        # The connected read model was built and threaded into ask_stream.
        assert getattr(captured["org_state"], "has_work", None) is True
        complete_frames = [f for f in frames if f["event"] == "complete"]
        assert len(complete_frames) == 1
        payload = _json.loads(complete_frames[0]["data"])
        assert payload["cited_records"] == [
            {
                "kind": "task",
                "record_id": sid("task-1"),
                "label": "Fix login",
                "status": "in_progress",
            }
        ]
