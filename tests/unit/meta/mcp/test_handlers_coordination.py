"""Unit tests for coordination-domain MCP handlers.

Covers the two handlers exposed by
``meta/mcp/handlers/coordination.py``:

- coordination: ``get_task_metrics``, ``metrics_list``

Each handler gets a focused test per branch (happy path, capability
gap, argument validation, not-found, service raise) so a future
regression in any one branch surfaces here instead of leaking into
the broader integration sweep.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.api.state import AppState
from synthorg.coordination.state import CoordinationStateSlice
from synthorg.core.agent import AgentIdentity
from synthorg.meta.mcp.handlers.coordination import COORDINATION_HANDLERS
from synthorg.observability.events.mcp import (
    MCP_HANDLER_ARGUMENT_INVALID,
    MCP_HANDLER_CAPABILITY_GAP,
    MCP_HANDLER_INVOKE_FAILED,
)
from tests._shared import JsonDict, make_app_state
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def actor() -> AgentIdentity:
    return make_test_actor(name="ops")


@pytest.fixture
def unwired_state() -> AppState:
    """App state with none of the coordination services attached.

    Every handler must route this to ``capability_gap`` / the
    ``not_supported`` envelope -- verifying that the
    ``has_<service>`` guards are wired correctly.
    """
    return make_app_state()


def _parse(raw: str) -> JsonDict:
    body: JsonDict = json.loads(raw)
    assert body["status"] in {"ok", "error"}, (
        f"legacy envelope leaked: status={body['status']!r}"
    )
    return body


# ── TestAllHandlersReturnValidEnvelope ────────────────────────────


class TestAllHandlersReturnValidEnvelope:
    """Every handler must emit a well-formed envelope with unwired state."""

    @pytest.mark.parametrize(
        "tool_name",
        list(COORDINATION_HANDLERS.keys()),
    )
    async def test_unwired_returns_capability_gap(
        self,
        tool_name: str,
        unwired_state: AppState,
        actor: AgentIdentity,
    ) -> None:
        handler = COORDINATION_HANDLERS[tool_name]
        # Common argument surface -- individual handlers ignore
        # fields they don't consume.
        args: JsonDict = {
            "task_id": "task-1",
            "decision_id": "decision-1",
            "agent_ids": ["agent-1"],
            "department": "engineering",
            "offset": 0,
            "limit": 10,
        }
        with structlog.testing.capture_logs() as events:
            raw = await handler(
                app_state=unwired_state,
                arguments=args,
                actor=actor,
            )
        body = _parse(raw)
        # Every unwired handler must surface a ``not_supported``
        # envelope paired with a CAPABILITY_GAP audit event.
        assert body["status"] == "error"
        assert body["domain_code"] == "not_supported"
        assert any(
            e.get("event") == MCP_HANDLER_CAPABILITY_GAP
            and e.get("tool_name") == tool_name
            for e in events
        ), (
            f"{tool_name} did not emit MCP_HANDLER_CAPABILITY_GAP; "
            f"events were {[e.get('event') for e in events]}"
        )


# ── Coordination ──────────────────────────────────────────────────


class TestGetTaskMetrics:
    """``synthorg_coordination_get_task_metrics`` -- read-only lookup."""

    async def test_happy_path(self, actor: AgentIdentity) -> None:
        record = SimpleNamespace(
            model_dump=lambda mode="json": {"task_id": "t-1", "team_size": 3},
        )
        service = AsyncMock()
        service.get_task_metrics.return_value = record
        state = make_app_state(
            slices={CoordinationStateSlice: {"coordination_service": service}},
        )
        handler = COORDINATION_HANDLERS["synthorg_coordination_get_task_metrics"]

        raw = await handler(
            app_state=state,
            arguments={"task_id": "t-1"},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "ok"
        assert body["data"] == {"task_id": "t-1", "team_size": 3}
        service.get_task_metrics.assert_awaited_once()

    async def test_missing_task_id_returns_invalid_argument(
        self,
        actor: AgentIdentity,
    ) -> None:
        state = make_app_state(
            slices={CoordinationStateSlice: {"coordination_service": AsyncMock()}},
        )
        handler = COORDINATION_HANDLERS["synthorg_coordination_get_task_metrics"]

        with structlog.testing.capture_logs() as events:
            raw = await handler(
                app_state=state,
                arguments={},
                actor=actor,
            )

        body = _parse(raw)
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"
        assert any(e.get("event") == MCP_HANDLER_ARGUMENT_INVALID for e in events)

    async def test_blank_task_id_returns_invalid_argument(
        self,
        actor: AgentIdentity,
    ) -> None:
        state = make_app_state(
            slices={CoordinationStateSlice: {"coordination_service": AsyncMock()}},
        )
        handler = COORDINATION_HANDLERS["synthorg_coordination_get_task_metrics"]

        raw = await handler(
            app_state=state,
            arguments={"task_id": "   "},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"

    async def test_service_raises_maps_to_err(
        self,
        actor: AgentIdentity,
    ) -> None:
        service = AsyncMock()
        service.get_task_metrics.side_effect = RuntimeError("store down")
        state = make_app_state(
            slices={CoordinationStateSlice: {"coordination_service": service}},
        )
        handler = COORDINATION_HANDLERS["synthorg_coordination_get_task_metrics"]

        raw = await handler(
            app_state=state,
            arguments={"task_id": "t-1"},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "error"

    async def test_no_record_returns_not_found(
        self,
        actor: AgentIdentity,
    ) -> None:
        service = AsyncMock()
        service.get_task_metrics.return_value = None
        state = make_app_state(
            slices={CoordinationStateSlice: {"coordination_service": service}},
        )
        handler = COORDINATION_HANDLERS["synthorg_coordination_get_task_metrics"]

        with structlog.testing.capture_logs() as events:
            raw = await handler(
                app_state=state,
                arguments={"task_id": "missing-task"},
                actor=actor,
            )

        body = _parse(raw)
        assert body["status"] == "error"
        assert body["domain_code"] == "not_found"
        # The 404 log must carry the task_id for operator triage.
        failed = [e for e in events if e.get("event") == MCP_HANDLER_INVOKE_FAILED]
        assert failed, "no MCP_HANDLER_INVOKE_FAILED emitted for not_found"
        assert failed[-1].get("task_id") == "missing-task"


class TestMetricsList:
    """``synthorg_coordination_metrics_list`` -- paged metrics."""

    async def test_happy_path_with_pagination(
        self,
        actor: AgentIdentity,
    ) -> None:
        records = [
            SimpleNamespace(
                model_dump=lambda mode="json", task=t: {"task_id": task},
            )
            for t in ("t-1", "t-2", "t-3")
        ]
        service = AsyncMock()
        service.list_metrics.return_value = (tuple(records), 42)
        state = make_app_state(
            slices={CoordinationStateSlice: {"coordination_service": service}},
        )
        handler = COORDINATION_HANDLERS["synthorg_coordination_metrics_list"]

        raw = await handler(
            app_state=state,
            arguments={"offset": 5, "limit": 3},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "ok"
        assert body["pagination"] == {
            "total": 42,
            "offset": 5,
            "limit": 3,
        }

    async def test_filters_forwarded_to_service(
        self,
        actor: AgentIdentity,
    ) -> None:
        service = AsyncMock()
        service.list_metrics.return_value = ((), 0)
        state = make_app_state(
            slices={CoordinationStateSlice: {"coordination_service": service}},
        )
        handler = COORDINATION_HANDLERS["synthorg_coordination_metrics_list"]

        raw = await handler(
            app_state=state,
            arguments={
                "task_id": "t-9",
                "agent_id": "a-7",
                "since": "2026-01-01T00:00:00+00:00",
                "until": "2026-02-01T00:00:00+00:00",
                "offset": 2,
                "limit": 5,
            },
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "ok"
        call = service.list_metrics.await_args
        assert call.kwargs["task_id"] == "t-9"
        assert call.kwargs["agent_id"] == "a-7"
        assert call.kwargs["since"] == datetime(2026, 1, 1, tzinfo=UTC)
        assert call.kwargs["until"] == datetime(2026, 2, 1, tzinfo=UTC)
        assert call.kwargs["offset"] == 2
        assert call.kwargs["limit"] == 5

    async def test_service_raises_maps_to_err(
        self,
        actor: AgentIdentity,
    ) -> None:
        service = AsyncMock()
        service.list_metrics.side_effect = RuntimeError("store down")
        state = make_app_state(
            slices={CoordinationStateSlice: {"coordination_service": service}},
        )
        handler = COORDINATION_HANDLERS["synthorg_coordination_metrics_list"]

        raw = await handler(
            app_state=state,
            arguments={},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "error"
