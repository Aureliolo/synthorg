"""Tests for workflow execution controller."""

from datetime import UTC, datetime
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.core.enums import WorkflowNodeType
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)

# ── Seed data ─────────────────────────────────────────────────────

_INTERNAL_PERSISTENCE_DETAIL = (
    "persistence: version mismatch on row 1234, expected v=42 got v=41"
)

_NOW = datetime.now(UTC)

_START_NODE = WorkflowNode(
    id="node-start",
    type=WorkflowNodeType.START,
    label="Start",
)
_END_NODE = WorkflowNode(
    id="node-end",
    type=WorkflowNodeType.END,
    label="End",
    position_x=200.0,
)
_TASK_NODE = WorkflowNode(
    id="node-task",
    type=WorkflowNodeType.TASK,
    label="Do work",
    position_x=100.0,
    config={"title": "Test Task", "task_type": "development"},
)
_EDGE_START_TO_TASK = WorkflowEdge(
    id="e1",
    source_node_id="node-start",
    target_node_id="node-task",
)
_EDGE_TASK_TO_END = WorkflowEdge(
    id="e2",
    source_node_id="node-task",
    target_node_id="node-end",
)

_VALID_DEFINITION = WorkflowDefinition(
    id="wfdef-test001",
    name="Test Workflow",
    created_by="test-user",
    nodes=(_START_NODE, _TASK_NODE, _END_NODE),
    edges=(_EDGE_START_TO_TASK, _EDGE_TASK_TO_END),
    created_at=_NOW,
    updated_at=_NOW,
)


def _seed_definition(
    test_client: TestClient[Any],
    definition: WorkflowDefinition | None = None,
) -> None:
    """Seed a workflow definition into the fake persistence."""
    defn = definition or _VALID_DEFINITION
    app_state = test_client.app.state.app_state
    repo = app_state.persistence.workflow_definitions
    repo._definitions[defn.id] = defn


# ── Activate endpoint ─────────────────────────────────────────────


class TestActivateWorkflow:
    """POST /api/v1/workflow-executions/activate/{id}."""

    @pytest.mark.unit
    def test_activate_success(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _seed_definition(test_client)
        resp = test_client.post(
            "/api/v1/workflow-executions/activate/wfdef-test001",
            json={"project": "test-project"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["definition_id"] == "wfdef-test001"
        assert body["data"]["status"] == "running"
        assert body["data"]["project"] == "test-project"

    @pytest.mark.unit
    def test_activate_not_found(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/workflow-executions/activate/nonexistent",
            json={"project": "proj"},
        )
        assert resp.status_code == 404

    @pytest.mark.unit
    def test_activate_invalid_definition(
        self,
        test_client: TestClient[Any],
    ) -> None:
        orphan_node = WorkflowNode(
            id="orphan",
            type=WorkflowNodeType.TASK,
            label="Orphan",
            config={"title": "Orphan"},
        )
        invalid_def = WorkflowDefinition(
            id="wfdef-invalid",
            name="Invalid",
            created_by="test",
            nodes=(_START_NODE, _TASK_NODE, orphan_node, _END_NODE),
            edges=(_EDGE_START_TO_TASK, _EDGE_TASK_TO_END),
            created_at=_NOW,
            updated_at=_NOW,
        )
        _seed_definition(test_client, invalid_def)
        resp = test_client.post(
            "/api/v1/workflow-executions/activate/wfdef-invalid",
            json={"project": "proj"},
        )
        assert resp.status_code == 422

    @pytest.mark.unit
    def test_activate_creates_node_executions(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _seed_definition(test_client)
        resp = test_client.post(
            "/api/v1/workflow-executions/activate/wfdef-test001",
            json={"project": "proj"},
        )
        assert resp.status_code == 201
        body = resp.json()
        node_execs = body["data"]["node_executions"]
        assert len(node_execs) == 3
        statuses = {ne["node_id"]: ne["status"] for ne in node_execs}
        assert statuses["node-start"] == "completed"
        assert statuses["node-task"] == "task_created"
        assert statuses["node-end"] == "completed"


# ── List executions endpoint ──────────────────────────────────────


class TestListExecutions:
    """GET /api/v1/workflow-executions/by-definition/{id}."""

    @pytest.mark.unit
    def test_list_empty(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get(
            "/api/v1/workflow-executions/by-definition/wfdef-test001",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    @pytest.mark.unit
    def test_list_after_activate(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _seed_definition(test_client)
        test_client.post(
            "/api/v1/workflow-executions/activate/wfdef-test001",
            json={"project": "proj"},
        )
        resp = test_client.get(
            "/api/v1/workflow-executions/by-definition/wfdef-test001",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1


# ── Get execution endpoint ────────────────────────────────────────


class TestGetExecution:
    """GET /api/v1/workflow-executions/{id}."""

    @pytest.mark.unit
    def test_get_not_found(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get(
            "/api/v1/workflow-executions/nonexistent",
        )
        assert resp.status_code == 404

    @pytest.mark.unit
    def test_get_after_activate(
        self,
        test_client: TestClient[Any],
    ) -> None:
        _seed_definition(test_client)
        activate_resp = test_client.post(
            "/api/v1/workflow-executions/activate/wfdef-test001",
            json={"project": "proj"},
        )
        exec_id = activate_resp.json()["data"]["id"]
        resp = test_client.get(
            f"/api/v1/workflow-executions/{exec_id}",
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == exec_id


# ── Cancel execution endpoint ─────────────────────────────────────


class TestCancelExecution:
    """POST /api/v1/workflow-executions/{id}/cancel."""

    @pytest.mark.unit
    def test_cancel_not_found(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/workflow-executions/nonexistent/cancel",
        )
        assert resp.status_code == 404

    # test_cancel_after_activate: omitted due to xdist worker
    # segfault (process crash, not Python exception) on Python 3.14
    # when the cancel handler returns a successful response.
    # Root cause: Litestar's Pydantic v1 compat layer + Python 3.14.
    # The cancel operation is fully tested at the service level:
    # tests/unit/engine/workflow/test_execution_service.py::TestCancelExecution
    # The cancel_not_found test above verifies the endpoint is routable.


# ── RFC 9457 envelope tests ───────────────────────────────────────


@pytest.mark.unit
class TestWorkflowExecutionControllerErrorEnvelope:
    """RFC 9457 envelope shape from centralised exception_handlers.

    Every error path surfaces through the registered handler in
    ``src/synthorg/api/exception_handlers.py``; the controller never
    builds its own ``Response`` envelope, so the structured
    ``error_detail`` block is consistent with the rest of the API.
    """

    @staticmethod
    def _patch_service(
        monkeypatch: pytest.MonkeyPatch,
        method: str,
        exc: BaseException,
    ) -> None:
        """Monkeypatch ``WorkflowExecutionService.<method>`` to raise ``exc``."""
        from synthorg.engine.workflow.execution_service import (
            WorkflowExecutionService,
        )

        async def _raise(*_args: object, **_kwargs: object) -> object:
            raise exc

        monkeypatch.setattr(WorkflowExecutionService, method, _raise)

    @pytest.mark.parametrize(
        ("error_factory", "expected_code"),
        [
            (
                lambda: _import_engine_error("WorkflowDefinitionInvalidError")(
                    "definition rejected at activation",
                ),
                "REQUEST_VALIDATION_ERROR",
            ),
            (
                lambda: _import_engine_error("WorkflowConditionEvalError")(
                    "condition expression failed",
                ),
                "REQUEST_VALIDATION_ERROR",
            ),
        ],
        ids=["invalid_definition", "condition_eval"],
    )
    def test_activate_validation_error_envelope(
        self,
        test_client: TestClient[Any],
        monkeypatch: pytest.MonkeyPatch,
        error_factory: Any,
        expected_code: str,
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        self._patch_service(monkeypatch, "activate", error_factory())

        resp = test_client.post(
            "/api/v1/workflow-executions/activate/wfdef-any",
            json={"project": "test-project"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode[expected_code]
        assert detail["error_category"] == ErrorCategory.VALIDATION
        assert detail["retryable"] is False

    @pytest.mark.parametrize(
        ("method", "request_spec"),
        [
            (
                "activate",
                {
                    "verb": "POST",
                    "path": "/api/v1/workflow-executions/activate/wfdef-any",
                    "json": {"project": "test-project"},
                },
            ),
            (
                "list_executions",
                {
                    "verb": "GET",
                    "path": "/api/v1/workflow-executions/by-definition/wfdef-any",
                },
            ),
            (
                "get_execution",
                {
                    "verb": "GET",
                    "path": "/api/v1/workflow-executions/wfexec-any",
                },
            ),
            (
                "cancel_execution",
                {
                    "verb": "POST",
                    "path": "/api/v1/workflow-executions/wfexec-any/cancel",
                },
            ),
        ],
        ids=["activate", "list", "get", "cancel"],
    )
    def test_persistence_failure_envelope(
        self,
        test_client: TestClient[Any],
        monkeypatch: pytest.MonkeyPatch,
        method: str,
        request_spec: dict[str, Any],
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
        from synthorg.core.persistence_errors import PersistenceError

        self._patch_service(monkeypatch, method, PersistenceError("DB unreachable"))

        verb = request_spec["verb"]
        path = request_spec["path"]
        resp = (
            test_client.get(path)
            if verb == "GET"
            else test_client.post(path, json=request_spec.get("json"))
        )
        assert resp.status_code == 500
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.PERSISTENCE_ERROR
        assert detail["error_category"] == ErrorCategory.INTERNAL
        assert detail["retryable"] is False

    @pytest.mark.parametrize(
        ("error_factory", "expected_code"),
        [
            (
                lambda: _import_persistence_error("PersistenceVersionConflictError")(
                    "version mismatch",
                ),
                "VERSION_CONFLICT",
            ),
            (
                lambda: _import_engine_error(
                    "WorkflowExecutionAlreadyTerminalError",
                )(
                    "execution already terminal",
                ),
                "WORKFLOW_EXECUTION_ALREADY_TERMINAL",
            ),
        ],
        ids=["version_race", "already_terminal"],
    )
    def test_cancel_conflict_envelope(
        self,
        test_client: TestClient[Any],
        monkeypatch: pytest.MonkeyPatch,
        error_factory: Any,
        expected_code: str,
    ) -> None:
        """Two cancel-conflict paths share status + category but differ in code.

        ``VERSION_CONFLICT`` (4002) signals a row-level optimistic-concurrency
        race; the caller can re-read and retry.
        ``WORKFLOW_EXECUTION_ALREADY_TERMINAL`` (4008) signals the execution
        finished before the cancel arrived; no retry will succeed.
        """
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        self._patch_service(monkeypatch, "cancel_execution", error_factory())

        resp = test_client.post(
            "/api/v1/workflow-executions/wfexec-any/cancel",
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode[expected_code]
        assert detail["error_category"] == ErrorCategory.CONFLICT
        assert detail["retryable"] is False

    def test_cancel_version_conflict_does_not_leak_persistence_detail(
        self,
        test_client: TestClient[Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Persistence-layer detail must not reach the public envelope.

        The cancel controller catches ``PersistenceVersionConflictError``
        and re-raises ``VersionConflictError`` with the default message,
        so internal persistence detail (row IDs, version numbers, raw
        SQL text) cannot leak into the response body. Locks the
        contract by injecting an implementation-specific exception
        message and asserting the envelope echoes none of its tokens.
        """
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        leaky_exc = _import_persistence_error("PersistenceVersionConflictError")(
            _INTERNAL_PERSISTENCE_DETAIL,
        )
        self._patch_service(monkeypatch, "cancel_execution", leaky_exc)

        resp = test_client.post(
            "/api/v1/workflow-executions/wfexec-any/cancel",
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.VERSION_CONFLICT
        assert detail["error_category"] == ErrorCategory.CONFLICT
        raw_body = resp.text
        assert "row 1234" not in raw_body
        assert "v=42" not in raw_body
        assert "v=41" not in raw_body
        assert "PersistenceVersionConflictError" not in raw_body


def _import_engine_error(name: str) -> type[BaseException]:
    """Defer engine-error imports so parametrize ids stay readable."""
    from synthorg.engine import errors as _engine_errors

    return getattr(_engine_errors, name)  # type: ignore[no-any-return]


def _import_persistence_error(name: str) -> type[BaseException]:
    """Defer persistence-error imports so parametrize ids stay readable."""
    from synthorg.core import persistence_errors as _persistence_errors

    return getattr(_persistence_errors, name)  # type: ignore[no-any-return]
