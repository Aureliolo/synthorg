"""Tests for workflow definition controller."""

from datetime import UTC, datetime
from typing import Any

import pytest
import structlog.testing
from litestar.testing import TestClient

from synthorg.core.enums import WorkflowNodeType, WorkflowType
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.observability.events.api import WORKFLOW_DEFINITION_CHANGED
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
    client: TestClient[Any],
    definition_id: str,
    *,
    name: str = "test-workflow",
    nodes: tuple[WorkflowNode, ...] = (_START_NODE, _TASK_NODE, _END_NODE),
    edges: tuple[WorkflowEdge, ...] = (_EDGE_S2T, _EDGE_T2E),
) -> str:
    """Seed a WorkflowDefinition into the fake repo and return its ID."""
    defn = WorkflowDefinition(
        id=definition_id,
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
    repo = client.app.state.app_state.persistence.workflow_definitions
    repo._definitions[defn.id] = defn
    return defn.id


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


def _create_workflow(
    client: TestClient[Any],
    **overrides: object,
) -> dict[str, Any]:
    """Create a workflow via POST and return the response JSON."""
    payload = _make_create_payload(**overrides)  # type: ignore[arg-type]
    resp = client.post(
        "/api/v1/workflows",
        json=payload,
        headers=make_auth_headers("ceo"),
    )
    assert resp.status_code == 201
    result: dict[str, Any] = resp.json()
    return result


@pytest.mark.unit
class TestWorkflowController:
    """Tests for WorkflowController CRUD, validation, and export."""

    # ── List ─────────────────────────────────────────────────────

    def test_list_workflows_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/workflows")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    def test_list_workflows_after_create(self, test_client: TestClient[Any]) -> None:
        _create_workflow(test_client, name="wf-alpha")
        _create_workflow(test_client, name="wf-beta")

        resp = test_client.get("/api/v1/workflows")
        assert resp.status_code == 200
        resp.json()

    def test_list_workflows_filter_by_type(self, test_client: TestClient[Any]) -> None:
        _create_workflow(
            test_client,
            name="wf-seq",
            workflow_type="sequential_pipeline",
        )
        _create_workflow(
            test_client,
            name="wf-par",
            workflow_type="parallel_execution",
        )

        resp = test_client.get("/api/v1/workflows?workflow_type=parallel_execution")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["name"] == "wf-par"

    @pytest.mark.parametrize(
        "bad_type",
        ["bogus", "not_a_type", "KANBAN"],
    )
    def test_list_workflows_invalid_type_filter(
        self,
        test_client: TestClient[Any],
        bad_type: str,
    ) -> None:
        resp = test_client.get(f"/api/v1/workflows?workflow_type={bad_type}")
        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert "Invalid workflow type" in body["error"]

    # ── Create ───────────────────────────────────────────────────

    def test_create_workflow(self, test_client: TestClient[Any]) -> None:
        body = _create_workflow(test_client, name="new-workflow")
        assert body["success"] is True
        data = body["data"]
        assert data["id"].startswith("wfdef-")
        assert data["name"] == "new-workflow"
        assert data["workflow_type"] == "sequential_pipeline"
        assert data["revision"] == 1
        assert data["version"] == "1.0.0"
        assert data["is_subworkflow"] is False
        assert len(data["nodes"]) == 3
        assert len(data["edges"]) == 2

    def test_create_workflow_emits_audit_event(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """create_workflow fires WORKFLOW_DEFINITION_CHANGED with action
        ``create`` so the audit chain captures schema-level mutations
        upstream of the well-instrumented execution layer."""
        with structlog.testing.capture_logs() as events:
            _create_workflow(test_client, name="audited-workflow")
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
        assert entry["definition_id"].startswith("wfdef-")

    def test_create_workflow_minimal_graph(self, test_client: TestClient[Any]) -> None:
        """START + END only, no edges."""
        body = _create_workflow(
            test_client,
            name="minimal",
            nodes=_MINIMAL_NODES,
            edges=_MINIMAL_EDGES,
        )
        assert body["success"] is True
        assert len(body["data"]["nodes"]) == 2
        assert len(body["data"]["edges"]) == 0

    # ── Get ──────────────────────────────────────────────────────

    def test_get_workflow(self, test_client: TestClient[Any]) -> None:
        created = _create_workflow(test_client)
        wf_id = created["data"]["id"]

        resp = test_client.get(f"/api/v1/workflows/{wf_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["id"] == wf_id
        assert body["data"]["name"] == "test-workflow"

    def test_get_workflow_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/workflows/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()

    # ── Update ───────────────────────────────────────────────────

    def test_update_workflow(self, test_client: TestClient[Any]) -> None:
        created = _create_workflow(test_client)
        wf_id = created["data"]["id"]

        resp = test_client.patch(
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

    def test_update_workflow_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.patch(
            "/api/v1/workflows/nonexistent",
            json={"name": "no-such-workflow"},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()

    def test_update_workflow_revision_conflict(
        self, test_client: TestClient[Any]
    ) -> None:
        created = _create_workflow(test_client)
        wf_id = created["data"]["id"]

        resp = test_client.patch(
            f"/api/v1/workflows/{wf_id}",
            json={"name": "conflict-name", "expected_revision": 999},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        assert "version conflict" in body["error"].lower()

    def test_update_workflow_with_correct_expected_revision(
        self, test_client: TestClient[Any]
    ) -> None:
        created = _create_workflow(test_client)
        wf_id = created["data"]["id"]

        resp = test_client.patch(
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

    def test_delete_workflow(self, test_client: TestClient[Any]) -> None:
        created = _create_workflow(test_client)
        wf_id = created["data"]["id"]

        del_resp = test_client.delete(
            f"/api/v1/workflows/{wf_id}",
            headers=make_auth_headers("ceo"),
        )
        assert del_resp.status_code == 204

        # Confirm it's gone.
        get_resp = test_client.get(f"/api/v1/workflows/{wf_id}")
        assert get_resp.status_code == 404

    def test_delete_workflow_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.delete(
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

    def test_validate_workflow_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post("/api/v1/workflows/nonexistent/validate")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()

    def test_validate_workflow(self, test_client: TestClient[Any]) -> None:
        """A valid 3-node graph should pass validation."""
        wf_id = _seed(test_client, "wfdef-val001")

        resp = test_client.post(
            f"/api/v1/workflows/{wf_id}/validate",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["valid"] is True
        assert body["data"]["errors"] == []

    def test_validate_workflow_with_errors(self, test_client: TestClient[Any]) -> None:
        """START + END with no edge -- END is unreachable."""
        wf_id = _seed(
            test_client,
            "wfdef-val002",
            name="disconnected",
            nodes=(_START_NODE, _END_NODE),
            edges=(),
        )

        resp = test_client.post(
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

    def test_export_workflow_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post("/api/v1/workflows/nonexistent/export")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()

    def test_export_workflow(self, test_client: TestClient[Any]) -> None:
        wf_id = _seed(test_client, "wfdef-exp001")

        resp = test_client.post(
            f"/api/v1/workflows/{wf_id}/export",
        )
        assert resp.status_code == 200
        assert "yaml" in resp.headers.get("content-type", "").lower()
        text = resp.text
        assert "workflow_definition" in text
        assert "test-workflow" in text
