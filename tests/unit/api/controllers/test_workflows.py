"""Tests for workflow definition controller."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
import structlog.testing

from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import WorkflowNodeType, WorkflowType
from synthorg.observability.events.api import WORKFLOW_DEFINITION_CHANGED
from synthorg.persistence.state import persistence_of
from tests._shared import JsonDict, LoopAsyncClient, as_pk
from tests.unit.api.conftest import make_auth_headers

# ── Minimal valid graph data (dicts for HTTP payloads) ───────────

_START_NODE_DICT: dict[str, object] = {
    "id": "node-start",
    "type": "start",
    "label": "Start",
    "position_x": 0.0,
    "position_y": 0.0,
}

_END_NODE_DICT: dict[str, object] = {
    "id": "node-end",
    "type": "end",
    "label": "End",
    "position_x": 200.0,
    "position_y": 0.0,
}

_TASK_NODE_DICT: dict[str, object] = {
    "id": "node-task-1",
    "type": "task",
    "label": "Do work",
    "position_x": 100.0,
    "position_y": 0.0,
    "config": {"title": "Implement feature"},
}

_EDGE_START_TO_TASK_DICT: dict[str, object] = {
    "id": "edge-1",
    "source_node_id": "node-start",
    "target_node_id": "node-task-1",
    "type": "sequential",
}

_EDGE_TASK_TO_END_DICT: dict[str, object] = {
    "id": "edge-2",
    "source_node_id": "node-task-1",
    "target_node_id": "node-end",
    "type": "sequential",
}

_MINIMAL_NODES = [_START_NODE_DICT, _END_NODE_DICT]
_MINIMAL_EDGES: list[dict[str, object]] = []

_THREE_NODE_NODES = [_START_NODE_DICT, _TASK_NODE_DICT, _END_NODE_DICT]
_THREE_NODE_EDGES = [_EDGE_START_TO_TASK_DICT, _EDGE_TASK_TO_END_DICT]


# ── Model-level constants for direct repository seeding ──────────

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
    id="node-task-1",
    type=WorkflowNodeType.TASK,
    label="Do work",
    position_x=100.0,
    config={"title": "Implement feature"},
)
_EDGE_S2T = WorkflowEdge(
    id="edge-1",
    source_node_id="node-start",
    target_node_id="node-task-1",
)
_EDGE_T2E = WorkflowEdge(
    id="edge-2",
    source_node_id="node-task-1",
    target_node_id="node-end",
)


def _seed(
    client: LoopAsyncClient,
    definition_id: str,
    *,
    name: str = "test-workflow",
    nodes: tuple[WorkflowNode, ...] = (_START_NODE, _TASK_NODE, _END_NODE),
    edges: tuple[WorkflowEdge, ...] = (_EDGE_S2T, _EDGE_T2E),
) -> str:
    """Seed a WorkflowDefinition into the fake repo and return its ID."""
    defn = WorkflowDefinition(
        id=as_pk(definition_id),
        name=name,
        description="A test workflow",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        nodes=nodes,
        edges=edges,
        created_by="api",
        created_at=_NOW,
        updated_at=_NOW,
    )
    # Direct mutation for synchronous test seeding -- bypasses async save()
    # since Litestar's TestClient runs in a sync context.
    repo = cast(Any, persistence_of(client.app.state.app_state).workflow_definitions)  # type: ignore[explicit-any]  # reach into fake repo internals
    repo._definitions[str(defn.id)] = defn
    return str(defn.id)


# ── HTTP payload helpers ─────────────────────────────────────────


def _make_create_payload(
    *,
    name: str = "test-workflow",
    description: str = "A test workflow",
    workflow_type: str = "sequential_pipeline",
    nodes: list[dict[str, object]] | None = None,
    edges: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build a valid workflow creation payload."""
    return {
        "name": name,
        "description": description,
        "workflow_type": workflow_type,
        "nodes": nodes if nodes is not None else _THREE_NODE_NODES,
        "edges": edges if edges is not None else _THREE_NODE_EDGES,
    }


async def _create_workflow(
    client: LoopAsyncClient,
    **overrides: object,
) -> JsonDict:
    """Create a workflow via POST and return the response JSON."""
    payload = _make_create_payload(**overrides)  # type: ignore[arg-type]
    resp = await client.post(
        "/api/v1/workflows",
        json=payload,
        headers=make_auth_headers("ceo"),
    )
    assert resp.status_code == 201
    result: JsonDict = resp.json()
    return result


@pytest.mark.unit
class TestWorkflowController:
    """Tests for WorkflowController CRUD, validation, and export."""

    # ── List ─────────────────────────────────────────────────────

    async def test_list_workflows_empty(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/workflows")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_list_workflows_after_create(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _create_workflow(async_test_client, name="wf-alpha")
        await _create_workflow(async_test_client, name="wf-beta")

        resp = await async_test_client.get("/api/v1/workflows")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["data"], list)
        names = {item.get("name") for item in body["data"]}
        assert {"wf-alpha", "wf-beta"} <= names

    async def test_list_workflows_filter_by_type(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await _create_workflow(
            async_test_client,
            name="wf-seq",
            workflow_type="sequential_pipeline",
        )
        await _create_workflow(
            async_test_client,
            name="wf-par",
            workflow_type="parallel_execution",
        )

        resp = await async_test_client.get(
            "/api/v1/workflows?workflow_type=parallel_execution"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["name"] == "wf-par"

    async def test_list_workflows_has_more_with_overflow(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        for i in range(4):
            await _create_workflow(async_test_client, name=f"wf-page-{i:02d}")

        resp = await async_test_client.get("/api/v1/workflows?limit=2")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["pagination"]["has_more"] is True
        assert body["pagination"]["next_cursor"] is not None

    @pytest.mark.parametrize(
        "bad_type",
        ["bogus", "not_a_type", "KANBAN"],
    )
    async def test_list_workflows_invalid_type_filter(
        self,
        async_test_client: LoopAsyncClient,
        bad_type: str,
    ) -> None:
        resp = await async_test_client.get(
            f"/api/v1/workflows?workflow_type={bad_type}"
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert "Invalid workflow type" in body["error"]

    # ── Create ───────────────────────────────────────────────────

    async def test_create_workflow(self, async_test_client: LoopAsyncClient) -> None:
        body = await _create_workflow(async_test_client, name="new-workflow")
        assert body["success"] is True
        data = body["data"]
        assert str(UUID(data["id"])) == data["id"]
        assert data["name"] == "new-workflow"
        assert data["workflow_type"] == "sequential_pipeline"
        assert data["revision"] == 1
        assert data["version"] == "1.0.0"
        assert data["is_subworkflow"] is False
        assert len(data["nodes"]) == 3
        assert len(data["edges"]) == 2

    async def test_create_workflow_emits_audit_event(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """create_workflow fires WORKFLOW_DEFINITION_CHANGED with action
        ``create`` so the audit chain captures schema-level mutations
        upstream of the well-instrumented execution layer."""
        with structlog.testing.capture_logs() as events:
            await _create_workflow(async_test_client, name="audited-workflow")
        # Use the event constant (resolved to its current string) so
        # renaming the constant in events/api.py automatically updates
        # this assertion -- protects against silent drift between the
        # controller's emitted event and the test's expected name.
        audit_events = [
            e for e in events if e.get("event") == WORKFLOW_DEFINITION_CHANGED
        ]
        assert len(audit_events) == 1
        entry = audit_events[0]
        assert entry["action"] == "create"
        assert entry["actor"]
        assert str(UUID(entry["definition_id"])) == entry["definition_id"]

    async def test_create_workflow_minimal_graph(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """START + END only, no edges."""
        body = await _create_workflow(
            async_test_client,
            name="minimal",
            nodes=_MINIMAL_NODES,
            edges=_MINIMAL_EDGES,
        )
        assert body["success"] is True
        assert len(body["data"]["nodes"]) == 2
        assert len(body["data"]["edges"]) == 0

    # ── Get ──────────────────────────────────────────────────────

    async def test_get_workflow(self, async_test_client: LoopAsyncClient) -> None:
        created = await _create_workflow(async_test_client)
        wf_id = created["data"]["id"]

        resp = await async_test_client.get(f"/api/v1/workflows/{wf_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["id"] == wf_id
        assert body["data"]["name"] == "test-workflow"

    async def test_get_workflow_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCode

        resp = await async_test_client.get("/api/v1/workflows/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()
        assert (
            body["error_detail"]["error_code"]
            == ErrorCode.WORKFLOW_DEFINITION_NOT_FOUND
        )

    # ── Update ───────────────────────────────────────────────────

    async def test_update_workflow(self, async_test_client: LoopAsyncClient) -> None:
        created = await _create_workflow(async_test_client)
        wf_id = created["data"]["id"]

        resp = await async_test_client.patch(
            f"/api/v1/workflows/{wf_id}",
            json={"name": "updated-name", "description": "new desc"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["name"] == "updated-name"
        assert body["data"]["description"] == "new desc"
        assert body["data"]["revision"] == 2

    async def test_update_workflow_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.patch(
            "/api/v1/workflows/nonexistent",
            json={"name": "no-such-workflow"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()

    async def test_update_workflow_revision_conflict(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        created = await _create_workflow(async_test_client)
        wf_id = created["data"]["id"]

        resp = await async_test_client.patch(
            f"/api/v1/workflows/{wf_id}",
            json={"name": "conflict-name", "expected_revision": 999},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        assert "revision conflict" in body["error"].lower()

    async def test_update_workflow_with_correct_expected_revision(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        created = await _create_workflow(async_test_client)
        wf_id = created["data"]["id"]

        resp = await async_test_client.patch(
            f"/api/v1/workflows/{wf_id}",
            json={
                "name": "versioned-update",
                "expected_revision": 1,
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["revision"] == 2

    # ── Delete ───────────────────────────────────────────────────

    async def test_delete_workflow(self, async_test_client: LoopAsyncClient) -> None:
        created = await _create_workflow(async_test_client)
        wf_id = created["data"]["id"]

        del_resp = await async_test_client.delete(
            f"/api/v1/workflows/{wf_id}",
            headers=make_auth_headers("ceo"),
        )
        assert del_resp.status_code == 204

        # Confirm it's gone.
        get_resp = await async_test_client.get(f"/api/v1/workflows/{wf_id}")
        assert get_resp.status_code == 404

    async def test_delete_workflow_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.delete(
            "/api/v1/workflows/nonexistent",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()

    # ── Validate ─────────────────────────────────────────────────
    #
    # Validate and export share a pre-seed pattern: the definition is
    # inserted directly into the fake repository to avoid a second
    # POST round-trip.  The validation/export logic itself is tested
    # exhaustively in tests/unit/engine/workflow/.

    async def test_validate_workflow_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCode

        resp = await async_test_client.post("/api/v1/workflows/nonexistent/validate")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()
        assert (
            body["error_detail"]["error_code"]
            == ErrorCode.WORKFLOW_DEFINITION_NOT_FOUND
        )

    async def test_validate_workflow(self, async_test_client: LoopAsyncClient) -> None:
        """A valid 3-node graph should pass validation."""
        wf_id = _seed(async_test_client, "wfdef-val001")

        resp = await async_test_client.post(
            f"/api/v1/workflows/{wf_id}/validate",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["valid"] is True
        assert body["data"]["errors"] == []

    async def test_validate_workflow_with_errors(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """START + END with no edge -- END is unreachable."""
        wf_id = _seed(
            async_test_client,
            "wfdef-val002",
            name="disconnected",
            nodes=(_START_NODE, _END_NODE),
            edges=(),
        )

        resp = await async_test_client.post(
            f"/api/v1/workflows/{wf_id}/validate",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["valid"] is False
        assert len(body["data"]["errors"]) > 0
        error_codes = [e["code"] for e in body["data"]["errors"]]
        assert "end_not_reachable" in error_codes

    # ── Export ───────────────────────────────────────────────────

    async def test_export_workflow_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCode

        resp = await async_test_client.post("/api/v1/workflows/nonexistent/export")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()
        assert (
            body["error_detail"]["error_code"]
            == ErrorCode.WORKFLOW_DEFINITION_NOT_FOUND
        )

    async def test_export_workflow(self, async_test_client: LoopAsyncClient) -> None:
        wf_id = _seed(async_test_client, "wfdef-exp001")

        resp = await async_test_client.post(
            f"/api/v1/workflows/{wf_id}/export",
        )
        assert resp.status_code == 200
        assert "yaml" in resp.headers.get("content-type", "").lower()
        text = resp.text
        assert "workflow_definition" in text


@pytest.mark.unit
class TestWorkflowControllerErrorEnvelope:
    """RFC 9457 envelope shape from centralised exception_handlers.

    Every error path in the controller must surface through the
    registered handler in ``src/synthorg/api/exception_handlers.py``
    rather than a controller-built ``Response``, so the structured
    ``error_detail`` block is consistent with the rest of the API.
    """

    async def test_list_invalid_workflow_type_envelope(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        resp = await async_test_client.get("/api/v1/workflows?workflow_type=bogus")
        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.REQUEST_VALIDATION_ERROR
        assert detail["error_category"] == ErrorCategory.VALIDATION
        assert detail["retryable"] is False

    async def test_create_workflow_invalid_definition_envelope(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """Pydantic validation failure surfaces a 422 with a safe message.

        The controller must not leak Pydantic validation detail; the
        response carries the typed ``WorkflowDefinitionValidationError``
        default message via the centralised handler.
        """
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        bad_payload = _make_create_payload(
            nodes=[
                {
                    "id": "node-start",
                    "type": "start",
                    "label": "Start",
                    "position_x": "not-a-float",
                    "position_y": 0.0,
                },
                _END_NODE_DICT,
            ],
        )
        resp = await async_test_client.post(
            "/api/v1/workflows",
            json=bad_payload,
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.REQUEST_VALIDATION_ERROR
        assert detail["error_category"] == ErrorCategory.VALIDATION
        assert detail["retryable"] is False
        assert "Invalid workflow definition." in body["error"]
        assert "ValidationError" not in body["error"]

    async def test_update_workflow_not_found_envelope(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        resp = await async_test_client.patch(
            "/api/v1/workflows/wfdef-missing",
            json={"name": "no-such-workflow"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.WORKFLOW_DEFINITION_NOT_FOUND
        assert detail["error_category"] == ErrorCategory.NOT_FOUND
        assert detail["retryable"] is False

    async def test_update_workflow_revision_conflict_envelope(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        created = await _create_workflow(async_test_client, name="conflict-target")
        wf_id = created["data"]["id"]

        resp = await async_test_client.patch(
            f"/api/v1/workflows/{wf_id}",
            json={"name": "racing-update", "expected_revision": 999},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.VERSION_CONFLICT
        assert detail["error_category"] == ErrorCategory.CONFLICT
        assert detail["retryable"] is False

    async def test_validate_workflow_invalid_definition_envelope(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """The /validate endpoint also routes invalid bodies through
        the centralised handler with a 422 envelope."""
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        bad_payload = _make_create_payload(
            nodes=[
                {
                    "id": "node-start",
                    "type": "start",
                    "label": "Start",
                    "position_x": "not-a-float",
                    "position_y": 0.0,
                },
                _END_NODE_DICT,
            ],
        )
        resp = await async_test_client.post(
            "/api/v1/workflows/validate-draft",
            json=bad_payload,
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.REQUEST_VALIDATION_ERROR
        assert detail["error_category"] == ErrorCategory.VALIDATION
        assert detail["retryable"] is False

    async def test_export_workflow_yaml_serialization_error_envelope(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ValueError from the YAML exporter surfaces a 422 envelope.

        The ``WorkflowYamlExportError`` ClassVar maps to 422 so clients of
        /workflows/{id}/export receive the documented status for an export
        serialization failure.
        """
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        wf_id = _seed(async_test_client, "wfdef-export-err")

        def _raise_value_error(_definition: object) -> str:
            msg = "yaml round-trip failed"
            raise ValueError(msg)

        monkeypatch.setattr(
            "synthorg.api.controllers.workflows.validation.export_workflow_yaml",
            _raise_value_error,
        )

        resp = await async_test_client.post(
            f"/api/v1/workflows/{wf_id}/export",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.REQUEST_VALIDATION_ERROR
        assert detail["error_category"] == ErrorCategory.VALIDATION
        assert detail["retryable"] is False
        assert "Export failed" in body["error"]

    async def test_create_from_blueprint_not_found_envelope(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing blueprint surfaces a 404 RFC 9457 envelope via the
        centralised handler; the controller must propagate
        ``BlueprintNotFoundError`` instead of building its own Response.
        """
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
        from synthorg.engine.workflow.blueprint_errors import BlueprintNotFoundError

        def _raise_not_found(name: str) -> object:
            msg = f"blueprint {name!r} not found"
            raise BlueprintNotFoundError(msg)

        monkeypatch.setattr(
            "synthorg.api.controllers._workflow_builders.load_blueprint",
            _raise_not_found,
        )

        resp = await async_test_client.post(
            "/api/v1/workflows/from-blueprint",
            json={"blueprint_name": "nonexistent"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.RESOURCE_NOT_FOUND
        assert detail["error_category"] == ErrorCategory.NOT_FOUND
        assert detail["retryable"] is False

    async def test_create_from_blueprint_validation_envelope(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Blueprint schema-validation failure surfaces a 422 envelope."""
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
        from synthorg.engine.workflow.blueprint_errors import (
            BlueprintValidationError,
        )

        def _raise_validation(name: str) -> object:
            msg = f"blueprint {name!r} schema invalid"
            raise BlueprintValidationError(msg)

        monkeypatch.setattr(
            "synthorg.api.controllers._workflow_builders.load_blueprint",
            _raise_validation,
        )

        resp = await async_test_client.post(
            "/api/v1/workflows/from-blueprint",
            json={"blueprint_name": "broken"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.VALIDATION_ERROR
        assert detail["error_category"] == ErrorCategory.VALIDATION
        assert detail["retryable"] is False

    async def test_update_workflow_invalid_payload_envelope(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Invalid update collection surfaces a 422 RFC 9457 envelope.

        ``_validate_collection`` raises a field-scoped
        ``WorkflowDefinitionValidationError`` so the central handler's
        envelope tells API clients which collection failed without
        leaking Pydantic detail.
        """
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        created = await _create_workflow(
            async_test_client, name="invalid-update-target"
        )
        wf_id = created["data"]["id"]

        resp = await async_test_client.patch(
            f"/api/v1/workflows/{wf_id}",
            json={
                "nodes": [
                    {
                        "id": "node-start",
                        "type": "start",
                        "label": "Start",
                        "position_x": "not-a-float",
                        "position_y": 0.0,
                    },
                    _END_NODE_DICT,
                ],
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.REQUEST_VALIDATION_ERROR
        assert detail["error_category"] == ErrorCategory.VALIDATION
        assert detail["retryable"] is False
        assert "Invalid nodes field in request." in body["error"]
        assert "ValidationError" not in body["error"]

    async def test_update_workflow_merged_invariant_envelope(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Merged-definition validation failures surface as 422.

        Each item in the update payload passes per-item validation
        (``_validate_collection`` accepts every node in isolation), but
        the merged ``WorkflowDefinition`` is rejected by the
        graph-level ``_validate_unique_ids`` model validator. This
        exercises ``apply_update``'s outer try/except branch (the one
        that wraps ``WorkflowDefinition.model_validate(merged)``) and
        confirms the envelope still hides Pydantic class names.
        """
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        created = await _create_workflow(
            async_test_client, name="merged-invariant-target"
        )
        wf_id = created["data"]["id"]

        duplicate_id_node: dict[str, object] = {
            "id": "node-start",
            "type": "task",
            "label": "Duplicate ID",
            "position_x": 50.0,
            "position_y": 0.0,
            "config": {"title": "Will collide with the start node"},
        }
        resp = await async_test_client.patch(
            f"/api/v1/workflows/{wf_id}",
            json={
                "nodes": [_START_NODE_DICT, duplicate_id_node, _END_NODE_DICT],
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.REQUEST_VALIDATION_ERROR
        assert detail["error_category"] == ErrorCategory.VALIDATION
        assert detail["retryable"] is False
        assert "Invalid workflow definition." in body["error"]
        assert "ValidationError" not in body["error"]
        assert "Duplicate node IDs" not in body["error"]
