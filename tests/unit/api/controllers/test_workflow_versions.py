"""Tests for workflow versioning API endpoints."""

from typing import Any

import pytest
from litestar.testing import TestClient

from tests.unit.api.conftest import make_auth_headers

# ── Helpers ──────────────────────────────────────────────────────

_THREE_NODE_NODES = [
    {"id": "node-start", "type": "start", "label": "Start"},
    {
        "id": "node-task-1",
        "type": "task",
        "label": "Do work",
        "position_x": 100.0,
        "config": {"title": "Test"},
    },
    {"id": "node-end", "type": "end", "label": "End", "position_x": 200.0},
]
_THREE_NODE_EDGES = [
    {
        "id": "edge-1",
        "source_node_id": "node-start",
        "target_node_id": "node-task-1",
        "type": "sequential",
    },
    {
        "id": "edge-2",
        "source_node_id": "node-task-1",
        "target_node_id": "node-end",
        "type": "sequential",
    },
]


def _create_workflow(
    test_client: TestClient[Any],
    **overrides: object,
) -> dict[str, Any]:
    """Create a workflow via POST and return the response data."""
    payload: dict[str, object] = {
        "name": "test-workflow",
        "description": "A test",
        "workflow_type": "sequential_pipeline",
        "nodes": _THREE_NODE_NODES,
        "edges": _THREE_NODE_EDGES,
    }
    payload.update(overrides)
    resp = test_client.post(
        "/api/v1/workflows",
        json=payload,
        headers=make_auth_headers("ceo"),
    )
    assert resp.status_code == 201
    result: dict[str, Any] = resp.json()["data"]
    return result


def _update_workflow(
    test_client: TestClient[Any],
    wf_id: str,
    expected_revision: int,
    **fields: object,
) -> dict[str, Any]:
    """PATCH a workflow and return response data."""
    payload: dict[str, object] = {"expected_revision": expected_revision}
    payload.update(fields)
    resp = test_client.patch(
        f"/api/v1/workflows/{wf_id}",
        json=payload,
        headers=make_auth_headers("ceo"),
    )
    assert resp.status_code == 200
    result: dict[str, Any] = resp.json()["data"]
    return result


# ── Auto-snapshot on create/update ────────────────────────────────


class TestAutoSnapshot:
    """Version snapshots are created automatically."""

    @pytest.mark.unit
    def test_create_creates_version(self, test_client: TestClient[Any]) -> None:
        wf = _create_workflow(test_client)
        resp = test_client.get(
            f"/api/v1/workflows/{wf['id']}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        versions = resp.json()["data"]
        assert len(versions) == 1
        assert versions[0]["version"] == 1

    @pytest.mark.unit
    def test_update_creates_version(self, test_client: TestClient[Any]) -> None:
        wf = _create_workflow(test_client)
        _update_workflow(test_client, wf["id"], 1, name="Updated Name")
        resp = test_client.get(
            f"/api/v1/workflows/{wf['id']}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        versions = resp.json()["data"]
        assert len(versions) == 2
        # Newest first.
        assert versions[0]["version"] == 2
        assert versions[1]["version"] == 1


# ── GET /workflows/{id}/versions ──────────────────────────────────


class TestListVersions:
    """List versions endpoint."""

    @pytest.mark.unit
    def test_empty_for_nonexistent(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get(
            "/api/v1/workflows/wfdef-nonexistent/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.unit
    def test_list_versions_ordering(self, test_client: TestClient[Any]) -> None:
        wf = _create_workflow(test_client, name="V1")
        wf_id = wf["id"]
        _update_workflow(test_client, wf_id, 1, name="V2")
        _update_workflow(test_client, wf_id, 2, name="V3")

        resp = test_client.get(
            f"/api/v1/workflows/{wf_id}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        versions = resp.json()["data"]
        assert len(versions) == 3
        # Newest first.
        assert [v["version"] for v in versions] == [3, 2, 1]
        assert versions[0]["snapshot"]["name"] == "V3"
        assert versions[2]["snapshot"]["name"] == "V1"

    @pytest.mark.unit
    def test_list_versions_paginated(self, test_client: TestClient[Any]) -> None:
        wf = _create_workflow(test_client, name="V1")
        wf_id = wf["id"]
        _update_workflow(test_client, wf_id, 1, name="V2")
        _update_workflow(test_client, wf_id, 2, name="V3")

        # First page: limit=2
        resp = test_client.get(
            f"/api/v1/workflows/{wf_id}/versions?limit=2",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["data"][0]["version"] == 3
        assert body["data"][1]["version"] == 2
        assert body["pagination"]["has_more"] is True
        next_cursor = body["pagination"]["next_cursor"]
        assert next_cursor is not None

        # Second page via opaque cursor.  Pass the cursor through
        # ``params`` rather than interpolating it into the URL so the
        # test does not couple to cursor-encoding details (e.g. if
        # future versions use a symbol that needs URL-escaping, the
        # interpolated form breaks silently while ``params`` hands it
        # to httpx as an opaque value).
        resp2 = test_client.get(
            f"/api/v1/workflows/{wf_id}/versions",
            params={"limit": 2, "cursor": next_cursor},
            headers=make_auth_headers("ceo"),
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert len(body2["data"]) == 1
        assert body2["data"][0]["version"] == 1
        assert body2["pagination"]["has_more"] is False
        # Terminal page must also clear ``next_cursor`` so clients do
        # not walk a dangling cursor past the end; the
        # ``_validate_cursor_consistency`` validator on PaginationMeta
        # enforces the ``has_more ↔ next_cursor`` pairing.
        assert body2["pagination"]["next_cursor"] is None


# ── GET /workflows/{id}/versions/{version} ────────────────────────


class TestGetVersion:
    """Get specific version endpoint."""

    @pytest.mark.unit
    def test_get_version(self, test_client: TestClient[Any]) -> None:
        wf = _create_workflow(test_client)
        resp = test_client.get(
            f"/api/v1/workflows/{wf['id']}/versions/1",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["version"] == 1
        assert resp.json()["data"]["snapshot"]["name"] == "test-workflow"

    @pytest.mark.unit
    def test_version_not_found(self, test_client: TestClient[Any]) -> None:
        wf = _create_workflow(test_client)
        resp = test_client.get(
            f"/api/v1/workflows/{wf['id']}/versions/99",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404


# ── GET /workflows/{id}/diff ──────────────────────────────────────


class TestDiff:
    """Diff computation endpoint."""

    @pytest.mark.unit
    def test_diff_between_versions(self, test_client: TestClient[Any]) -> None:
        wf = _create_workflow(test_client)
        _update_workflow(test_client, wf["id"], 1, name="Renamed Workflow")
        resp = test_client.get(
            f"/api/v1/workflows/{wf['id']}/diff",
            params={"from_version": 1, "to_version": 2},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        diff = resp.json()["data"]
        assert diff["from_version"] == 1
        assert diff["to_version"] == 2
        # Name changed should appear in metadata_changes.
        meta_fields = [m["field"] for m in diff["metadata_changes"]]
        assert "name" in meta_fields

    @pytest.mark.unit
    def test_diff_same_version_returns_validation_error(
        self,
        test_client: TestClient[Any],
    ) -> None:
        wf = _create_workflow(test_client)
        resp = test_client.get(
            f"/api/v1/workflows/{wf['id']}/diff",
            params={"from_version": 1, "to_version": 1},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422

    @pytest.mark.unit
    def test_diff_version_not_found(self, test_client: TestClient[Any]) -> None:
        wf = _create_workflow(test_client)
        resp = test_client.get(
            f"/api/v1/workflows/{wf['id']}/diff",
            params={"from_version": 1, "to_version": 99},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404


# ── POST /workflows/{id}/rollback ─────────────────────────────────


class TestRollback:
    """Rollback endpoint."""

    @pytest.mark.unit
    def test_rollback_success(self, test_client: TestClient[Any]) -> None:
        # 1. Create a workflow with name "Original" (auto-creates v1).
        wf = _create_workflow(test_client, name="Original")
        wf_id = wf["id"]

        # 2. Update it to name "Updated" (auto-creates v2).
        _update_workflow(test_client, wf_id, 1, name="Updated")

        # 3. POST rollback to v1 (definition revision is now 2).
        resp = test_client.post(
            f"/api/v1/workflows/{wf_id}/rollback",
            json={"target_version": 1, "expected_revision": 2},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Original"

        # 4. Verify version history has v3 with name "Original".
        hist_resp = test_client.get(
            f"/api/v1/workflows/{wf_id}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert hist_resp.status_code == 200
        versions = hist_resp.json()["data"]
        assert len(versions) == 3
        # Newest first -- v3 should be the rollback snapshot.
        assert versions[0]["version"] == 3
        assert versions[0]["snapshot"]["name"] == "Original"

    @pytest.mark.unit
    def test_rollback_revision_conflict(self, test_client: TestClient[Any]) -> None:
        wf = _create_workflow(test_client)
        resp = test_client.post(
            f"/api/v1/workflows/{wf['id']}/rollback",
            json={"target_version": 1, "expected_revision": 99},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409

    @pytest.mark.unit
    def test_rollback_definition_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            "/api/v1/workflows/wfdef-nonexistent/rollback",
            json={"target_version": 1, "expected_revision": 2},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
