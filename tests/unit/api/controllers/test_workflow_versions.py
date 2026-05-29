"""Tests for workflow versioning API endpoints."""

from typing import Any

import pytest

from synthorg.api.services.workflow_rollback_service import WorkflowRollbackService
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from tests._shared import LoopAsyncClient
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


async def _create_workflow(
    async_test_client: LoopAsyncClient,
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
    resp = await async_test_client.post(
        "/api/v1/workflows",
        json=payload,
        headers=make_auth_headers("ceo"),
    )
    assert resp.status_code == 201
    result: dict[str, Any] = resp.json()["data"]
    return result


async def _update_workflow(
    async_test_client: LoopAsyncClient,
    wf_id: str,
    expected_revision: int,
    **fields: object,
) -> dict[str, Any]:
    """PATCH a workflow and return response data."""
    payload: dict[str, object] = {"expected_revision": expected_revision}
    payload.update(fields)
    resp = await async_test_client.patch(
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
    async def test_create_creates_version(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        wf = await _create_workflow(async_test_client)
        resp = await async_test_client.get(
            f"/api/v1/workflows/{wf['id']}/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        versions = resp.json()["data"]
        assert len(versions) == 1
        assert versions[0]["version"] == 1

    @pytest.mark.unit
    async def test_update_creates_version(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        wf = await _create_workflow(async_test_client)
        await _update_workflow(async_test_client, wf["id"], 1, name="Updated Name")
        resp = await async_test_client.get(
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
    async def test_empty_for_nonexistent(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/workflows/wfdef-nonexistent/versions",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @pytest.mark.unit
    async def test_list_versions_ordering(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        wf = await _create_workflow(async_test_client, name="V1")
        wf_id = wf["id"]
        await _update_workflow(async_test_client, wf_id, 1, name="V2")
        await _update_workflow(async_test_client, wf_id, 2, name="V3")

        resp = await async_test_client.get(
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
    async def test_list_versions_paginated(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        wf = await _create_workflow(async_test_client, name="V1")
        wf_id = wf["id"]
        await _update_workflow(async_test_client, wf_id, 1, name="V2")
        await _update_workflow(async_test_client, wf_id, 2, name="V3")

        # First page: limit=2
        resp = await async_test_client.get(
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
        resp2 = await async_test_client.get(
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
    async def test_get_version(self, async_test_client: LoopAsyncClient) -> None:
        wf = await _create_workflow(async_test_client)
        resp = await async_test_client.get(
            f"/api/v1/workflows/{wf['id']}/versions/1",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["version"] == 1
        assert resp.json()["data"]["snapshot"]["name"] == "test-workflow"

    @pytest.mark.unit
    async def test_version_not_found(self, async_test_client: LoopAsyncClient) -> None:
        wf = await _create_workflow(async_test_client)
        resp = await async_test_client.get(
            f"/api/v1/workflows/{wf['id']}/versions/99",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404


# ── GET /workflows/{id}/diff ──────────────────────────────────────


class TestDiff:
    """Diff computation endpoint."""

    @pytest.mark.unit
    async def test_diff_between_versions(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        wf = await _create_workflow(async_test_client)
        await _update_workflow(async_test_client, wf["id"], 1, name="Renamed Workflow")
        resp = await async_test_client.get(
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
    async def test_diff_same_version_returns_validation_error(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        wf = await _create_workflow(async_test_client)
        resp = await async_test_client.get(
            f"/api/v1/workflows/{wf['id']}/diff",
            params={"from_version": 1, "to_version": 1},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        from synthorg.core.error_taxonomy import (
            ErrorCategory,
            ErrorCode,
        )

        assert detail["error_code"] == ErrorCode.VALIDATION_ERROR
        assert detail["error_category"] == ErrorCategory.VALIDATION
        assert detail["retryable"] is False

    @pytest.mark.unit
    async def test_diff_version_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        wf = await _create_workflow(async_test_client)
        resp = await async_test_client.get(
            f"/api/v1/workflows/{wf['id']}/diff",
            params={"from_version": 1, "to_version": 99},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404


# ── POST /workflows/{id}/rollback ─────────────────────────────────


class TestRollback:
    """Rollback endpoint."""

    @pytest.mark.unit
    async def test_rollback_success(self, async_test_client: LoopAsyncClient) -> None:
        # 1. Create a workflow with name "Original" (auto-creates v1).
        wf = await _create_workflow(async_test_client, name="Original")
        wf_id = wf["id"]

        # 2. Update it to name "Updated" (auto-creates v2).
        await _update_workflow(async_test_client, wf_id, 1, name="Updated")

        # 3. POST rollback to v1 (definition revision is now 2).
        resp = await async_test_client.post(
            f"/api/v1/workflows/{wf_id}/rollback",
            json={"target_version": 1, "expected_revision": 2},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Original"

        # 4. Verify version history has v3 with name "Original".
        hist_resp = await async_test_client.get(
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
    async def test_rollback_revision_conflict(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        wf = await _create_workflow(async_test_client)
        resp = await async_test_client.post(
            f"/api/v1/workflows/{wf['id']}/rollback",
            json={"target_version": 1, "expected_revision": 99},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409

    @pytest.mark.unit
    async def test_rollback_definition_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/workflows/wfdef-nonexistent/rollback",
            json={"target_version": 1, "expected_revision": 2},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    @pytest.mark.unit
    async def test_rollback_persistence_version_conflict_translates_to_409(
        self,
        async_test_client: LoopAsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A late persistence-layer concurrency miss surfaces as 409.

        The controller catches ``PersistenceVersionConflictError`` from
        the rollback service and re-raises the HTTP-aware
        ``VersionConflictError`` so the centralised RFC 9457 dispatch
        produces a 409 response. Without that translation the persistence
        error would escape uncaught and become a generic 500.
        """
        wf = await _create_workflow(async_test_client, name="Original")
        wf_id = wf["id"]
        await _update_workflow(async_test_client, wf_id, 1, name="Updated")

        # Patch the ``rollback`` method at the class level so the
        # AppState-wired instance picks up the stub via normal attribute
        # lookup. ``WorkflowRollbackService`` uses ``__slots__``, which
        # makes per-instance method substitution a no-go;
        # ``workflow_rollback_service`` is also a read-only property on
        # ``AppState``. Class-level patching is the supported path.
        async def _raise_conflict(
            self: WorkflowRollbackService,
            rolled_back: object,
            *,
            target_version: int,
            saved_by: object,
        ) -> None:
            msg = "racing write"
            raise PersistenceVersionConflictError(msg)

        monkeypatch.setattr(WorkflowRollbackService, "rollback", _raise_conflict)

        resp = await async_test_client.post(
            f"/api/v1/workflows/{wf_id}/rollback",
            json={"target_version": 1, "expected_revision": 2},
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        detail = body["error_detail"]
        assert detail["error_code"] == ErrorCode.VERSION_CONFLICT
        assert detail["error_category"] == ErrorCategory.CONFLICT
