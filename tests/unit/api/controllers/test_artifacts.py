"""Tests for artifact controller."""

import pytest

from synthorg.engine.workspace.state import WorkspaceStateSlice
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers


@pytest.mark.unit
class TestArtifactController:
    async def test_list_artifacts_empty(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/artifacts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_get_artifact_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/artifacts/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()

    async def test_create_and_get_artifact(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        create_resp = await async_test_client.post(
            "/api/v1/artifacts",
            json={
                "type": "code",
                "path": "src/auth/login.py",
                "task_id": "task-123",
                "created_by": "agent-1",
                "description": "Login endpoint",
            },
            headers=make_auth_headers("ceo"),
        )
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["success"] is True
        artifact_id = created["data"]["id"]
        assert artifact_id.startswith("artifact-")
        assert created["data"]["type"] == "code"
        assert created["data"]["path"] == "src/auth/login.py"

        get_resp = await async_test_client.get(f"/api/v1/artifacts/{artifact_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["id"] == artifact_id

    async def test_list_artifacts_after_create(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await async_test_client.post(
            "/api/v1/artifacts",
            json={
                "type": "code",
                "path": "src/a.py",
                "task_id": "task-1",
                "created_by": "agent-1",
            },
            headers=make_auth_headers("ceo"),
        )
        await async_test_client.post(
            "/api/v1/artifacts",
            json={
                "type": "tests",
                "path": "tests/a.py",
                "task_id": "task-1",
                "created_by": "agent-1",
            },
            headers=make_auth_headers("ceo"),
        )
        resp = await async_test_client.get("/api/v1/artifacts")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["data"], list)
        # Both created artifacts must appear in the listing -- a weak
        # ``len(...) >= 1`` would still pass if one of them silently
        # disappeared between create and list.
        returned_paths = {item.get("path") for item in body["data"]}
        assert {"src/a.py", "tests/a.py"}.issubset(returned_paths)

    async def test_list_artifacts_filter_by_task_id(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        await async_test_client.post(
            "/api/v1/artifacts",
            json={
                "type": "code",
                "path": "src/a.py",
                "task_id": "task-A",
                "created_by": "agent-1",
            },
            headers=make_auth_headers("ceo"),
        )
        await async_test_client.post(
            "/api/v1/artifacts",
            json={
                "type": "code",
                "path": "src/b.py",
                "task_id": "task-B",
                "created_by": "agent-1",
            },
            headers=make_auth_headers("ceo"),
        )
        resp = await async_test_client.get("/api/v1/artifacts?task_id=task-A")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["task_id"] == "task-A"

    async def test_list_artifacts_filter_by_invalid_type(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/artifacts?type=bogus")
        # Domain ``ValidationError`` maps to 422 via the central
        # exception handler.
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert "Invalid artifact type" in body["error"]

    async def test_oversized_artifact_id_rejected(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        long_id = "x" * 129
        resp = await async_test_client.get(
            f"/api/v1/artifacts/{long_id}",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    async def test_delete_artifact(self, async_test_client: LoopAsyncClient) -> None:
        create_resp = await async_test_client.post(
            "/api/v1/artifacts",
            json={
                "type": "code",
                "path": "src/del.py",
                "task_id": "task-del",
                "created_by": "agent-1",
            },
            headers=make_auth_headers("ceo"),
        )
        artifact_id = create_resp.json()["data"]["id"]
        del_resp = await async_test_client.delete(
            f"/api/v1/artifacts/{artifact_id}",
            headers=make_auth_headers("ceo"),
        )
        assert del_resp.status_code == 200
        # Confirm it's gone.
        get_resp = await async_test_client.get(f"/api/v1/artifacts/{artifact_id}")
        assert get_resp.status_code == 404

    async def test_delete_artifact_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.delete(
            "/api/v1/artifacts/nonexistent",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404

    async def test_download_content(self, async_test_client: LoopAsyncClient) -> None:
        """Pre-populate storage via public API, then test download."""
        create_resp = await async_test_client.post(
            "/api/v1/artifacts",
            json={
                "type": "code",
                "path": "src/dl.py",
                "task_id": "task-dl",
                "created_by": "agent-1",
                "content_type": "text/plain",
            },
            headers=make_auth_headers("ceo"),
        )
        artifact_id = create_resp.json()["data"]["id"]
        payload = b"hello world"
        storage = async_test_client.app.state.app_state.slice(
            WorkspaceStateSlice
        ).artifact_storage
        await storage.store(artifact_id, payload)
        dl_resp = await async_test_client.get(
            f"/api/v1/artifacts/{artifact_id}/content"
        )
        assert dl_resp.status_code == 200
        assert dl_resp.content == payload
        assert "attachment" in dl_resp.headers.get("content-disposition", "")

    async def test_download_content_not_found(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/artifacts/nonexistent/content")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False

    async def test_download_content_missing_bytes(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """Artifact exists in DB but content not in storage."""
        create_resp = await async_test_client.post(
            "/api/v1/artifacts",
            json={
                "type": "code",
                "path": "src/ghost.py",
                "task_id": "task-ghost",
                "created_by": "agent-1",
            },
            headers=make_auth_headers("ceo"),
        )
        artifact_id = create_resp.json()["data"]["id"]
        # Do not upload content -- storage has no bytes.
        resp = await async_test_client.get(f"/api/v1/artifacts/{artifact_id}/content")
        assert resp.status_code == 404
        body = resp.json()
        # Error message includes the artifact_id for parity with the
        # other 404 messages in this controller.
        error_lower = body["error"].lower()
        assert "content" in error_lower
        assert "not found" in error_lower
        assert artifact_id.lower() in error_lower
