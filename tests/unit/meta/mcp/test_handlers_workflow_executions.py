"""Unit tests for workflow-execution MCP handlers.

Focuses on the advertised filters and start payload reaching the
execution service unchanged: a status filter on ``list`` and the
project / context forwarding on ``start``.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.workflow.enums import WorkflowExecutionStatus
from synthorg.meta.mcp.handlers.workflow_executions import (
    workflow_executions_list,
    workflow_executions_start,
)
from tests._shared import JsonDict, make_app_state
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


@pytest.fixture
def actor() -> AgentIdentity:
    return make_test_actor(name="ops")


def _parse(raw: str) -> JsonDict:
    body: JsonDict = json.loads(raw)
    return body


async def test_list_forwards_status_filter(actor: AgentIdentity) -> None:
    service = AsyncMock()
    service.list_executions.return_value = ()
    state = make_app_state(
        slices={EngineStateSlice: {"workflow_execution_service": service}},
    )

    raw = await workflow_executions_list(
        app_state=state,
        arguments={"status": WorkflowExecutionStatus.RUNNING.value, "limit": 5},
        actor=actor,
    )

    body = _parse(raw)
    assert body["status"] == "ok"
    call = service.list_executions.await_args
    assert call.kwargs["status"] == WorkflowExecutionStatus.RUNNING


async def test_start_forwards_project_and_context(actor: AgentIdentity) -> None:
    execution = SimpleNamespace(
        model_dump=lambda mode="json": {"id": "exec-1"},
    )
    service = AsyncMock()
    service.activate.return_value = execution
    state = make_app_state(
        slices={EngineStateSlice: {"workflow_execution_service": service}},
    )
    context = {"key": "value"}

    raw = await workflow_executions_start(
        app_state=state,
        arguments={
            "workflow_id": "wf-1",
            "project": "alpha",
            "context": context,
        },
        actor=actor,
    )

    body = _parse(raw)
    assert body["status"] == "ok"
    call = service.activate.await_args
    assert call.args[0] == "wf-1"
    assert call.kwargs["project"] == "alpha"
    assert call.kwargs["context"] == {"key": "value"}
    # The handler deep-copies context, so a later caller mutation must
    # not reach the value the service already received.
    context["key"] = "mutated"
    assert call.kwargs["context"] == {"key": "value"}
