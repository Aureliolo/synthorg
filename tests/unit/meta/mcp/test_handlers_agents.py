# mypy: disable-error-code="explicit-any"
"""Smoke tests for agent domain MCP handlers.

The handler universe is big and half of it shims onto services that
don't yet expose a clean read method (personality registry, activity
feed, etc.).  The unit suite here covers:

- Every handler is callable with an empty/minimal arg dict and returns
  a syntactically valid envelope (``status`` is ``"ok"`` or
  ``"error"``; the legacy ``"not_implemented"`` status is explicitly
  rejected by the ``_parse`` helper below).  This is the regression
  guard.
- For tools that DO have a clean service shim, a happy-path test
  exercises the service call.
- ``synthorg_agents_delete`` gets the full destructive-op workout
  (guardrail branches + audit event on success).
"""

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.api.state import AppState
from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    CollaborationCalibration,
    CollaborationScoreResult,
)
from synthorg.meta.mcp.handlers.agents import AGENT_HANDLERS
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_GUARDRAIL_VIOLATED,
)
from tests._shared import make_app_state
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


@pytest.fixture
def identity() -> AgentIdentity:
    """Real :class:`AgentIdentity` so the fixture tracks the live contract.

    Handlers read ``.id``, ``.name``, and ``.model_dump()``; a
    ``SimpleNamespace`` stub silently diverges whenever a new required
    field lands on ``AgentIdentity``. Using the real Pydantic model
    means those diffs surface here as test failures instead.
    """
    return make_test_actor(name="alpha")


@pytest.fixture
def fake_agent_registry(identity: AgentIdentity) -> AsyncMock:
    registry = AsyncMock()
    registry.list_active.return_value = (identity,)
    registry.get_by_name.return_value = identity
    registry.get.return_value = identity
    registry.unregister.return_value = identity
    return registry


@pytest.fixture
def fake_performance_tracker(identity: AgentIdentity) -> AsyncMock:
    tracker = AsyncMock()
    tracker.get_snapshot.return_value = AgentPerformanceSnapshot(
        agent_id=NotBlankStr(str(identity.id)),
        computed_at=datetime.now(UTC),
    )
    tracker.get_collaboration_score.return_value = CollaborationScoreResult(
        score=0.75,
        strategy_name="test-strategy",
        confidence=0.9,
    )
    tracker.get_collaboration_calibration.return_value = CollaborationCalibration(
        agent_id=NotBlankStr(str(identity.id)),
        strategy_name=NotBlankStr("test-strategy"),
        sample_size=0,
    )
    return tracker


@pytest.fixture
def fake_app_state(
    fake_agent_registry: AsyncMock,
    fake_performance_tracker: AsyncMock,
) -> AppState:
    """App state stub with registry/performance mocks."""
    return make_app_state(
        agent_registry=fake_agent_registry,
        performance_tracker=fake_performance_tracker,
    )


@pytest.fixture
def actor() -> AgentIdentity:
    return make_test_actor(name="ops")


def _parse(result: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(result)
    assert body["status"] in {"ok", "error"}, (
        f"legacy envelope leaked: status={body['status']!r}"
    )
    return body


class TestAllAgentHandlersReturnValidEnvelope:
    """Every handler must return a well-formed envelope on a basic call."""

    @pytest.mark.parametrize(
        "tool_name",
        list(AGENT_HANDLERS.keys()),
    )
    async def test_basic_envelope(
        self,
        tool_name: str,
        fake_app_state: AppState,
        actor: AgentIdentity,
    ) -> None:
        handler = AGENT_HANDLERS[tool_name]
        # Minimal arg set likely accepted by each handler; destructive
        # ops fail guardrails but still return a valid envelope.
        args: dict[str, Any] = {
            "agent_name": "alpha",
            "agent_id": "agent-1",
            "name": "alpha",
            "role": "engineer",
            "department": "Engineering",
            "session_id": "sess-1",
            "level": "FULL",
        }
        result = await handler(
            app_state=fake_app_state,
            arguments=args,
            actor=actor,
        )
        _parse(result)


class TestAgentsList:
    async def test_happy_path(
        self,
        fake_app_state: AppState,
        identity: AgentIdentity,
    ) -> None:
        handler = AGENT_HANDLERS["synthorg_agents_list"]
        result = await handler(
            app_state=fake_app_state,
            arguments={},
            actor=None,
        )
        body = _parse(result)
        assert body["status"] == "ok"
        assert body["data"] == [identity.model_dump(mode="json")]


class TestAgentsGet:
    async def test_not_found(
        self,
        fake_app_state: AppState,
        fake_agent_registry: AsyncMock,
    ) -> None:
        fake_agent_registry.get_by_name.return_value = None
        handler = AGENT_HANDLERS["synthorg_agents_get"]
        body = _parse(
            await handler(
                app_state=fake_app_state,
                arguments={"agent_name": "missing"},
                actor=None,
            ),
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "not_found"


class TestAgentsDelete:
    """Full destructive-op workout."""

    async def test_happy_path_fires_audit_event(
        self,
        fake_app_state: AppState,
        identity: AgentIdentity,
        actor: AgentIdentity,
    ) -> None:
        handler = AGENT_HANDLERS["synthorg_agents_delete"]
        with structlog.testing.capture_logs() as logs:
            body = _parse(
                await handler(
                    app_state=fake_app_state,
                    arguments={
                        "agent_name": "alpha",
                        "reason": "retiring role",
                        "confirm": True,
                    },
                    actor=actor,
                ),
            )
        assert body["status"] == "ok"
        audit = [e for e in logs if e.get("event") == MCP_ADMIN_OP_EXECUTED]
        assert len(audit) == 1

    async def test_missing_confirm_blocked(
        self,
        fake_app_state: AppState,
        actor: AgentIdentity,
    ) -> None:
        handler = AGENT_HANDLERS["synthorg_agents_delete"]
        with structlog.testing.capture_logs() as logs:
            body = _parse(
                await handler(
                    app_state=fake_app_state,
                    arguments={"agent_name": "alpha", "reason": "x"},
                    actor=actor,
                ),
            )
        assert body["status"] == "error"
        assert body["domain_code"] == "guardrail_violated"
        events = {e.get("event") for e in logs}
        assert MCP_HANDLER_GUARDRAIL_VIOLATED in events
        assert MCP_ADMIN_OP_EXECUTED not in events

    async def test_missing_actor_blocked(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = AGENT_HANDLERS["synthorg_agents_delete"]
        body = _parse(
            await handler(
                app_state=fake_app_state,
                arguments={
                    "agent_name": "alpha",
                    "reason": "x",
                    "confirm": True,
                },
                actor=None,
            ),
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "guardrail_violated"

    async def test_not_found(
        self,
        fake_app_state: AppState,
        actor: AgentIdentity,
        fake_agent_registry: AsyncMock,
    ) -> None:
        fake_agent_registry.get_by_name.return_value = None
        handler = AGENT_HANDLERS["synthorg_agents_delete"]
        body = _parse(
            await handler(
                app_state=fake_app_state,
                arguments={
                    "agent_name": "missing",
                    "reason": "cleanup",
                    "confirm": True,
                },
                actor=actor,
            ),
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "not_found"


class TestWriteHandlersValidateInputs:
    """The META-MCP-3 write handlers reject empty/malformed input."""

    @pytest.mark.parametrize(
        "tool_name",
        [
            "synthorg_agents_create",
            "synthorg_agents_update",
            "synthorg_autonomy_update",
            "synthorg_collaboration_get_calibration",
        ],
    )
    async def test_empty_args_returns_invalid_argument(
        self,
        tool_name: str,
        fake_app_state: AppState,
    ) -> None:
        handler = AGENT_HANDLERS[tool_name]
        body = _parse(
            await handler(app_state=fake_app_state, arguments={}, actor=None),
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"
