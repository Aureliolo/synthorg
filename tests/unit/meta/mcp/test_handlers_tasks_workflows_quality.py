"""Smoke + destructive-op tests for tasks and workflows handlers."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity
from synthorg.meta.mcp.handlers.tasks import TASK_HANDLERS
from synthorg.meta.mcp.handlers.workflows import WORKFLOW_HANDLERS
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_GUARDRAIL_VIOLATED,
)
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


def _parse(result: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(result)
    assert body["status"] in {"ok", "error"}, (
        f"legacy envelope leaked: status={body['status']!r}"
    )
    return body


# --- tasks ----------------------------------------------------------------


@pytest.fixture
def task() -> SimpleNamespace:
    return SimpleNamespace(
        id="task-1",
        title="hello",
        model_dump=lambda mode="json": {"id": "task-1", "title": "hello"},
    )


@pytest.fixture
def task_app_state(task: SimpleNamespace) -> SimpleNamespace:
    engine = AsyncMock()
    engine.list_tasks.return_value = ((task,), 1)
    engine.get_task.return_value = task
    engine.update_task.return_value = task
    engine.cancel_task.return_value = (task, None)
    engine.delete_task.return_value = True
    engine.transition_task.return_value = (task, None)
    return SimpleNamespace(task_engine=engine)


@pytest.fixture
def actor() -> AgentIdentity:
    return make_test_actor(name="ops")


class TestTasksSmoke:
    @pytest.mark.parametrize("tool_name", list(TASK_HANDLERS.keys()))
    async def test_envelope(
        self,
        tool_name: str,
        task_app_state: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handler = TASK_HANDLERS[tool_name]
        args: dict[str, Any] = {
            "task_id": "task-1",
            "title": "x",
            "description": "y",
            "project": "p",
            "updates": {"title": "new"},
            "target_status": "in_progress",
        }
        _parse(await handler(app_state=task_app_state, arguments=args, actor=actor))


class TestTasksList:
    async def test_happy(self, task_app_state: SimpleNamespace) -> None:
        body = _parse(
            await TASK_HANDLERS["synthorg_tasks_list"](
                app_state=task_app_state,
                arguments={},
                actor=None,
            ),
        )
        assert body["status"] == "ok"


class TestTasksCancel:
    async def test_happy_fires_audit(
        self,
        task_app_state: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handler = TASK_HANDLERS["synthorg_tasks_cancel"]
        with structlog.testing.capture_logs() as logs:
            body = _parse(
                await handler(
                    app_state=task_app_state,
                    arguments={"task_id": "t1", "reason": "obsolete", "confirm": True},
                    actor=actor,
                ),
            )
        assert body["status"] == "ok"
        assert any(e.get("event") == MCP_ADMIN_OP_EXECUTED for e in logs)

    async def test_missing_reason(
        self,
        task_app_state: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        body = _parse(
            await TASK_HANDLERS["synthorg_tasks_cancel"](
                app_state=task_app_state,
                arguments={"task_id": "t1", "confirm": True},
                actor=actor,
            ),
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "guardrail_violated"


class TestTasksDelete:
    async def test_happy_fires_audit(
        self,
        task_app_state: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handler = TASK_HANDLERS["synthorg_tasks_delete"]
        with structlog.testing.capture_logs() as logs:
            body = _parse(
                await handler(
                    app_state=task_app_state,
                    arguments={"task_id": "t1", "reason": "cleanup", "confirm": True},
                    actor=actor,
                ),
            )
        assert body["status"] == "ok"
        assert any(e.get("event") == MCP_ADMIN_OP_EXECUTED for e in logs)

    async def test_missing_confirm(
        self,
        task_app_state: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            body = _parse(
                await TASK_HANDLERS["synthorg_tasks_delete"](
                    app_state=task_app_state,
                    arguments={"task_id": "t1", "reason": "x"},
                    actor=actor,
                ),
            )
        assert body["domain_code"] == "guardrail_violated"
        assert any(e.get("event") == MCP_HANDLER_GUARDRAIL_VIOLATED for e in logs)


class TestTasksCreate:
    async def test_invalid_task_data_returns_invalid_argument(
        self,
        task_app_state: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        body = _parse(
            await TASK_HANDLERS["synthorg_tasks_create"](
                app_state=task_app_state,
                arguments={"title": "x"},  # missing task_data dict
                actor=actor,
            ),
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"


# --- workflows ------------------------------------------------------------


@pytest.fixture
def workflow_def() -> SimpleNamespace:
    return SimpleNamespace(
        id="wf-1",
        revision=1,
        model_dump=lambda mode="json": {"id": "wf-1", "revision": 1},
    )


@pytest.fixture
def workflow_app_state(workflow_def: SimpleNamespace) -> SimpleNamespace:
    from synthorg.engine.workflow.service import WorkflowService

    def_repo = AsyncMock()
    def_repo.list_definitions.return_value = (workflow_def,)
    def_repo.get.return_value = workflow_def
    def_repo.delete.return_value = True

    version_repo = AsyncMock()
    version_repo.delete_versions_for_entity.return_value = 0

    persistence = SimpleNamespace(
        workflow_definitions=def_repo,
        workflow_versions=version_repo,
    )
    workflow_service = WorkflowService(
        definition_repo=def_repo,
        version_repo=version_repo,
    )
    return SimpleNamespace(
        persistence=persistence,
        workflow_service=workflow_service,
    )


class TestWorkflowsSmoke:
    @pytest.mark.parametrize("tool_name", list(WORKFLOW_HANDLERS.keys()))
    async def test_envelope(
        self,
        tool_name: str,
        workflow_app_state: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handler = WORKFLOW_HANDLERS[tool_name]
        args: dict[str, Any] = {
            "workflow_id": "wf-1",
            "subworkflow_id": "sw-1",
            "execution_id": "ex-1",
            "name": "x",
            "steps": [],
            "version_num": 1,
            "updates": {},
        }
        _parse(
            await handler(app_state=workflow_app_state, arguments=args, actor=actor),
        )


class TestWorkflowsList:
    async def test_happy(self, workflow_app_state: SimpleNamespace) -> None:
        body = _parse(
            await WORKFLOW_HANDLERS["synthorg_workflows_list"](
                app_state=workflow_app_state,
                arguments={},
                actor=None,
            ),
        )
        assert body["status"] == "ok"


class TestWorkflowsDelete:
    async def test_happy_fires_audit(
        self,
        workflow_app_state: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handler = WORKFLOW_HANDLERS["synthorg_workflows_delete"]
        with structlog.testing.capture_logs() as logs:
            body = _parse(
                await handler(
                    app_state=workflow_app_state,
                    arguments={
                        "workflow_id": "wf-1",
                        "reason": "retire",
                        "confirm": True,
                    },
                    actor=actor,
                ),
            )
        assert body["status"] == "ok"
        assert any(e.get("event") == MCP_ADMIN_OP_EXECUTED for e in logs)


# --- quality --------------------------------------------------------------
# All quality handlers are live shims backed by QualityFacadeService /
# ReviewFacadeService / EvaluationVersionService; per-tool tests live in
# test_handlers_quality.py.
