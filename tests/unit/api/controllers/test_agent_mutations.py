"""Tests for agent mutation endpoints (POST, PATCH, DELETE agents)."""

import pytest
import structlog.testing

from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers


@pytest.mark.unit
class TestCreateAgent:
    async def test_create_agent_happy_path(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # First create a department
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        resp = await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "developer",
                "department": "eng",
                "level": "senior",
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["name"] == "alice"
        assert data["role"] == "developer"
        assert data["department"] == "eng"
        assert data["level"] == "senior"

    async def test_create_agent_nonexistent_dept_422(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "developer",
                "department": "nonexistent",
                "level": "mid",
            },
        )
        assert resp.status_code == 422

    async def test_create_agent_duplicate_name_409(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        resp = await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "tester",
                "department": "eng",
                "level": "mid",
            },
        )
        assert resp.status_code == 409

    async def test_create_agent_observer_denied(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        async_test_client.headers.update(make_auth_headers("observer"))
        resp = await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestUpdateAgent:
    async def test_update_agent_happy_path(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        resp = await async_test_client.patch(
            "/api/v1/agents/alice",
            json={"level": "senior"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["level"] == "senior"

    async def test_update_agent_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.patch(
            "/api/v1/agents/nonexistent",
            json={"level": "senior"},
        )
        assert resp.status_code == 404

    async def test_update_agent_emits_audit_identity_modified(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """update_agent fires AGENT_IDENTITY_MODIFIED audit event with the
        actor and changed-field set so forensic investigators can
        reconstruct identity edits."""
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        with structlog.testing.capture_logs() as events:
            resp = await async_test_client.patch(
                "/api/v1/agents/alice",
                json={"level": "senior"},
            )
        assert resp.status_code == 200
        identity_events = [
            e for e in events if e.get("event") == "audit.agent.identity_modified"
        ]
        assert len(identity_events) == 1
        entry = identity_events[0]
        assert entry["agent_name"] == "alice"
        assert "level" in entry["fields_changed"]
        assert entry["actor"]  # non-empty


@pytest.mark.unit
class TestDeleteAgent:
    async def test_delete_agent_happy_path(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        resp = await async_test_client.delete("/api/v1/agents/alice")
        assert resp.status_code == 204

    async def test_delete_agent_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.delete("/api/v1/agents/nonexistent")
        assert resp.status_code == 404

    async def test_delete_agent_emits_audit_pair(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """``delete_agent`` emits ``audit.agent.deletion_requested`` BEFORE
        the persistence delete and ``audit.agent.deleted`` AFTER it
        succeeds.  Verify both events fire in the correct order on a
        happy-path delete so the audit trail captures intent on
        failure AND confirmation only on actual success.
        """
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        with structlog.testing.capture_logs() as events:
            resp = await async_test_client.delete("/api/v1/agents/alice")
        assert resp.status_code == 204

        # Filter to the two audit events of interest (order in
        # ``events`` reflects emission order, which is what the
        # before-then-after contract demands).
        audit_events = [
            e
            for e in events
            if e.get("event")
            in ("audit.agent.deletion_requested", "audit.agent.deleted")
        ]
        assert len(audit_events) == 2
        assert audit_events[0]["event"] == "audit.agent.deletion_requested"
        assert audit_events[1]["event"] == "audit.agent.deleted"
        # Both entries carry the same identifying context so an
        # operator inspecting the audit trail can pair the
        # request with its confirmation without joining on
        # timestamps.
        for entry in audit_events:
            assert entry["agent_name"] == "alice"
            assert entry["actor"]

    async def test_delete_c_suite_agent_409(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "exec"},
        )
        await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "chief",
                "role": "ceo",
                "department": "exec",
                "level": "c_suite",
            },
        )
        resp = await async_test_client.delete("/api/v1/agents/chief")
        assert resp.status_code == 409


@pytest.mark.unit
class TestUpdateAgentETag:
    async def test_stale_etag_returns_409(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        # Send a stale ETag
        resp = await async_test_client.patch(
            "/api/v1/agents/alice",
            json={"level": "senior"},
            headers={"If-Match": '"stale-etag-value000"'},
        )
        assert resp.status_code == 409

    async def test_matching_etag_allows_update(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        # Create agent and capture ETag
        await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "bob",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        # First update to get an ETag in the response
        resp1 = await async_test_client.patch(
            "/api/v1/agents/bob",
            json={"level": "senior"},
        )
        assert resp1.status_code == 200
        etag = resp1.headers.get("etag")
        assert etag is not None

        # Use the returned ETag for a second update
        resp2 = await async_test_client.patch(
            "/api/v1/agents/bob",
            json={"level": "lead"},
            headers={"If-Match": etag},
        )
        assert resp2.status_code == 200

    async def test_no_if_match_header_bypasses_check(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        await async_test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        await async_test_client.post(
            "/api/v1/agents",
            json={
                "name": "carol",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        # No If-Match header -- should succeed
        resp = await async_test_client.patch(
            "/api/v1/agents/carol",
            json={"level": "senior"},
        )
        assert resp.status_code == 200
