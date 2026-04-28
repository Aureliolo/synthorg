"""Unit tests for coordination-domain MCP handlers.

Covers the nine handlers exposed by
``meta/mcp/handlers/coordination.py``:

- coordination: ``get_task_metrics``, ``metrics_list``
- scaling: ``list_decisions``, ``get_decision``, ``get_config``,
  ``trigger``
- ceremony policy: ``get``, ``get_resolved``, ``get_active_strategy``

Each handler gets a focused test per branch (happy path, capability
gap, argument validation, not-found, service raise) so a future
regression in any one branch surfaces here instead of leaking into
the broader integration sweep.
"""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity
from synthorg.core.domain_errors import NotFoundError
from synthorg.meta.mcp.handlers.coordination import COORDINATION_HANDLERS
from synthorg.observability.events.mcp import (
    MCP_HANDLER_ARGUMENT_INVALID,
    MCP_HANDLER_CAPABILITY_GAP,
    MCP_HANDLER_INVOKE_FAILED,
)
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def actor() -> AgentIdentity:
    return make_test_actor(name="ops")


@pytest.fixture
def unwired_state() -> SimpleNamespace:
    """App state with none of the coordination services attached.

    Every handler must route this to ``capability_gap`` / the
    ``not_supported`` envelope -- verifying that the
    ``has_<service>`` guards are wired correctly.
    """
    return SimpleNamespace(
        has_coordination_service=False,
        has_scaling_decision_service=False,
        has_ceremony_policy_service=False,
    )


def _parse(raw: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(raw)
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
        unwired_state: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handler = COORDINATION_HANDLERS[tool_name]
        # Common argument surface -- individual handlers ignore
        # fields they don't consume.
        args: dict[str, Any] = {
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
        state = SimpleNamespace(
            has_coordination_service=True,
            coordination_service=service,
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
        state = SimpleNamespace(has_coordination_service=True)
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
        state = SimpleNamespace(has_coordination_service=True)
        handler = COORDINATION_HANDLERS["synthorg_coordination_get_task_metrics"]

        raw = await handler(
            app_state=state,
            arguments={"task_id": "   "},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"

    async def test_no_record_returns_not_found(
        self,
        actor: AgentIdentity,
    ) -> None:
        service = AsyncMock()
        service.get_task_metrics.return_value = None
        state = SimpleNamespace(
            has_coordination_service=True,
            coordination_service=service,
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
        state = SimpleNamespace(
            has_coordination_service=True,
            coordination_service=service,
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

    async def test_service_raises_maps_to_err(
        self,
        actor: AgentIdentity,
    ) -> None:
        service = AsyncMock()
        service.list_metrics.side_effect = RuntimeError("store down")
        state = SimpleNamespace(
            has_coordination_service=True,
            coordination_service=service,
        )
        handler = COORDINATION_HANDLERS["synthorg_coordination_metrics_list"]

        raw = await handler(
            app_state=state,
            arguments={},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "error"


# ── Scaling ───────────────────────────────────────────────────────


class TestScalingListDecisions:
    async def test_happy_path(self, actor: AgentIdentity) -> None:
        decision = SimpleNamespace(
            model_dump=lambda mode="json": {"id": "d-1"},
        )
        service = AsyncMock()
        service.list_decisions.return_value = ((decision,), 1)
        state = SimpleNamespace(
            has_scaling_decision_service=True,
            scaling_decision_service=service,
        )
        handler = COORDINATION_HANDLERS["synthorg_scaling_list_decisions"]

        raw = await handler(
            app_state=state,
            arguments={"offset": 0, "limit": 10},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "ok"
        assert body["pagination"]["total"] == 1
        assert body["data"] == [{"id": "d-1"}]


class TestScalingGetDecision:
    async def test_happy_path(self, actor: AgentIdentity) -> None:
        decision = SimpleNamespace(
            model_dump=lambda mode="json": {"id": "d-42"},
        )
        service = AsyncMock()
        service.get_decision.return_value = decision
        state = SimpleNamespace(
            has_scaling_decision_service=True,
            scaling_decision_service=service,
        )
        handler = COORDINATION_HANDLERS["synthorg_scaling_get_decision"]

        raw = await handler(
            app_state=state,
            arguments={"decision_id": "d-42"},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "ok"
        assert body["data"] == {"id": "d-42"}

    async def test_missing_decision_maps_to_not_found(
        self,
        actor: AgentIdentity,
    ) -> None:
        service = AsyncMock()
        service.get_decision.return_value = None
        state = SimpleNamespace(
            has_scaling_decision_service=True,
            scaling_decision_service=service,
        )
        handler = COORDINATION_HANDLERS["synthorg_scaling_get_decision"]

        with structlog.testing.capture_logs() as events:
            raw = await handler(
                app_state=state,
                arguments={"decision_id": "nonexistent"},
                actor=actor,
            )

        body = _parse(raw)
        assert body["domain_code"] == "not_found"
        failed = [e for e in events if e.get("event") == MCP_HANDLER_INVOKE_FAILED]
        assert failed
        assert failed[-1].get("decision_id") == "nonexistent"


class TestScalingGetConfig:
    async def test_happy_path(self, actor: AgentIdentity) -> None:
        config = SimpleNamespace(
            model_dump=lambda mode="json": {"min": 1, "max": 10},
        )
        service = AsyncMock()
        service.get_config.return_value = config
        state = SimpleNamespace(
            has_scaling_decision_service=True,
            scaling_decision_service=service,
        )
        handler = COORDINATION_HANDLERS["synthorg_scaling_get_config"]

        raw = await handler(
            app_state=state,
            arguments={},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "ok"
        assert body["data"] == {"min": 1, "max": 10}


class TestScalingTrigger:
    async def test_happy_path(self, actor: AgentIdentity) -> None:
        decision = SimpleNamespace(
            model_dump=lambda mode="json": {"id": "d-new"},
        )
        service = AsyncMock()
        service.trigger.return_value = (decision,)
        state = SimpleNamespace(
            has_scaling_decision_service=True,
            scaling_decision_service=service,
        )
        handler = COORDINATION_HANDLERS["synthorg_scaling_trigger"]

        raw = await handler(
            app_state=state,
            arguments={"agent_ids": ["agent-1", "agent-2"]},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "ok"
        assert body["data"] == [{"id": "d-new"}]

    @pytest.mark.parametrize(
        ("arguments", "expected_match"),
        [
            ({}, "list of non-blank strings"),
            ({"agent_ids": "not-a-list"}, "list of non-blank strings"),
            ({"agent_ids": []}, "non-empty list"),
            ({"agent_ids": ["", "valid"]}, "non-blank string"),
        ],
        ids=[
            "missing_agent_ids",
            "agent_ids_not_list",
            "empty_list",
            "blank_item",
        ],
    )
    async def test_invalid_arguments_mapped(
        self,
        actor: AgentIdentity,
        arguments: dict[str, Any],
        expected_match: str,
    ) -> None:
        state = SimpleNamespace(has_scaling_decision_service=True)
        handler = COORDINATION_HANDLERS["synthorg_scaling_trigger"]

        raw = await handler(
            app_state=state,
            arguments=arguments,
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"
        assert expected_match in body.get("message", "")


# ── Ceremony policy ──────────────────────────────────────────────


class TestCeremonyPolicyGet:
    async def test_happy_path(self, actor: AgentIdentity) -> None:
        policy = SimpleNamespace(
            model_dump=lambda mode="json": {"strategy": "task_driven"},
        )
        service = AsyncMock()
        service.get_policy.return_value = policy
        state = SimpleNamespace(
            has_ceremony_policy_service=True,
            ceremony_policy_service=service,
        )
        handler = COORDINATION_HANDLERS["synthorg_ceremony_policy_get"]

        raw = await handler(
            app_state=state,
            arguments={},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "ok"
        assert body["data"] == {"strategy": "task_driven"}


class TestCeremonyPolicyGetResolved:
    async def test_happy_path_no_department(
        self,
        actor: AgentIdentity,
    ) -> None:
        resolved = SimpleNamespace(
            model_dump=lambda mode="json": {"strategy": {"value": "hybrid"}},
        )
        service = AsyncMock()
        service.get_resolved_policy.return_value = resolved
        state = SimpleNamespace(
            has_ceremony_policy_service=True,
            ceremony_policy_service=service,
        )
        handler = COORDINATION_HANDLERS["synthorg_ceremony_policy_get_resolved"]

        raw = await handler(
            app_state=state,
            arguments={},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "ok"
        service.get_resolved_policy.assert_awaited_once_with(department=None)

    async def test_happy_path_with_department(
        self,
        actor: AgentIdentity,
    ) -> None:
        resolved = SimpleNamespace(
            model_dump=lambda mode="json": {"department": "engineering"},
        )
        service = AsyncMock()
        service.get_resolved_policy.return_value = resolved
        state = SimpleNamespace(
            has_ceremony_policy_service=True,
            ceremony_policy_service=service,
        )
        handler = COORDINATION_HANDLERS["synthorg_ceremony_policy_get_resolved"]

        raw = await handler(
            app_state=state,
            arguments={"department": "engineering"},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "ok"
        # The service receives a stripped ``NotBlankStr`` matching the
        # input (implicit normalization). Assert the value separately
        # so a future signature change (kwarg name rename) fails loudly.
        call = service.get_resolved_policy.await_args
        assert str(call.kwargs["department"]) == "engineering"

    @pytest.mark.parametrize(
        "bad_value",
        [None, "", "   "],
        ids=["null", "empty_string", "whitespace"],
    )
    async def test_rejects_null_or_blank_department(
        self,
        actor: AgentIdentity,
        bad_value: Any,
    ) -> None:
        service = AsyncMock()
        state = SimpleNamespace(
            has_ceremony_policy_service=True,
            ceremony_policy_service=service,
        )
        handler = COORDINATION_HANDLERS["synthorg_ceremony_policy_get_resolved"]

        raw = await handler(
            app_state=state,
            arguments={"department": bad_value},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"
        service.get_resolved_policy.assert_not_awaited()

    async def test_service_raises_not_found_propagates(
        self,
        actor: AgentIdentity,
    ) -> None:
        service = AsyncMock()
        service.get_resolved_policy.side_effect = NotFoundError(
            "department 'eng' not found",
        )
        state = SimpleNamespace(
            has_ceremony_policy_service=True,
            ceremony_policy_service=service,
        )
        handler = COORDINATION_HANDLERS["synthorg_ceremony_policy_get_resolved"]

        raw = await handler(
            app_state=state,
            arguments={"department": "eng"},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "error"


class TestCeremonyPolicyGetActiveStrategy:
    async def test_happy_path(self, actor: AgentIdentity) -> None:
        active = SimpleNamespace(
            model_dump=lambda mode="json": {
                "strategy": None,
                "sprint_id": None,
            },
        )
        service = AsyncMock()
        service.get_active_strategy.return_value = active
        state = SimpleNamespace(
            has_ceremony_policy_service=True,
            ceremony_policy_service=service,
        )
        handler = COORDINATION_HANDLERS["synthorg_ceremony_policy_get_active_strategy"]

        raw = await handler(
            app_state=state,
            arguments={},
            actor=actor,
        )

        body = _parse(raw)
        assert body["status"] == "ok"
        assert body["data"] == {"strategy": None, "sprint_id": None}
