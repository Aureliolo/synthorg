"""Tests for agent mutation endpoints (POST, PATCH, DELETE agents)."""

from typing import Any

import pytest
import structlog.testing
from litestar.testing import TestClient

from tests.unit.api.conftest import make_auth_headers


@pytest.mark.unit
class TestCreateAgent:
    def test_create_agent_happy_path(
        self,
        test_client: TestClient[Any],
    ) -> None:
        # First create a department
        test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        resp = test_client.post(
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

    def test_create_agent_nonexistent_dept_422(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "developer",
                "department": "nonexistent",
                "level": "mid",
            },
        )
        assert resp.status_code == 422

    def test_create_agent_duplicate_name_409(
        self,
        test_client: TestClient[Any],
    ) -> None:
        test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        resp = test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "tester",
                "department": "eng",
                "level": "mid",
            },
        )
        assert resp.status_code == 409

    def test_create_agent_observer_denied(
        self,
        test_client: TestClient[Any],
    ) -> None:
        test_client.headers.update(make_auth_headers("observer"))
        resp = test_client.post(
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
    def test_update_agent_happy_path(
        self,
        test_client: TestClient[Any],
    ) -> None:
        test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        resp = test_client.patch(
            "/api/v1/agents/alice",
            json={"level": "senior"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["level"] == "senior"

    def test_update_agent_not_found(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.patch(
            "/api/v1/agents/nonexistent",
            json={"level": "senior"},
        )
        assert resp.status_code == 404

    def test_update_agent_emits_audit_identity_modified(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """update_agent fires AGENT_IDENTITY_MODIFIED audit event with the
        actor and changed-field set so forensic investigators can
        reconstruct identity edits."""
        test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        with structlog.testing.capture_logs() as events:
            resp = test_client.patch(
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
    def test_delete_agent_happy_path(
        self,
        test_client: TestClient[Any],
    ) -> None:
        test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        resp = test_client.delete("/api/v1/agents/alice")
        assert resp.status_code == 204

    def test_delete_agent_not_found(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.delete("/api/v1/agents/nonexistent")
        assert resp.status_code == 404

    def test_delete_agent_emits_audit_before_persist(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """delete_agent fires AGENT_DELETED_AUDIT BEFORE the persistence
        delete so the audit captures intent even if the delete fails.
        Verify the event is emitted on a happy-path delete."""
        test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        with structlog.testing.capture_logs() as events:
            resp = test_client.delete("/api/v1/agents/alice")
        assert resp.status_code == 204
        deleted_events = [e for e in events if e.get("event") == "audit.agent.deleted"]
        assert len(deleted_events) == 1
        entry = deleted_events[0]
        assert entry["agent_name"] == "alice"
        assert entry["actor"]

    def test_delete_c_suite_agent_409(
        self,
        test_client: TestClient[Any],
    ) -> None:
        test_client.post(
            "/api/v1/departments",
            json={"name": "exec"},
        )
        test_client.post(
            "/api/v1/agents",
            json={
                "name": "chief",
                "role": "ceo",
                "department": "exec",
                "level": "c_suite",
            },
        )
        resp = test_client.delete("/api/v1/agents/chief")
        assert resp.status_code == 409


@pytest.mark.unit
class TestUpdateAgentETag:
    def test_stale_etag_returns_409(
        self,
        test_client: TestClient[Any],
    ) -> None:
        test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        test_client.post(
            "/api/v1/agents",
            json={
                "name": "alice",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        # Send a stale ETag
        resp = test_client.patch(
            "/api/v1/agents/alice",
            json={"level": "senior"},
            headers={"If-Match": '"stale-etag-value000"'},
        )
        assert resp.status_code == 409

    def test_matching_etag_allows_update(
        self,
        test_client: TestClient[Any],
    ) -> None:
        test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        # Create agent and capture ETag
        test_client.post(
            "/api/v1/agents",
            json={
                "name": "bob",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        # First update to get an ETag in the response
        resp1 = test_client.patch(
            "/api/v1/agents/bob",
            json={"level": "senior"},
        )
        assert resp1.status_code == 200
        etag = resp1.headers.get("etag")
        assert etag is not None

        # Use the returned ETag for a second update
        resp2 = test_client.patch(
            "/api/v1/agents/bob",
            json={"level": "lead"},
            headers={"If-Match": etag},
        )
        assert resp2.status_code == 200

    def test_no_if_match_header_bypasses_check(
        self,
        test_client: TestClient[Any],
    ) -> None:
        test_client.post(
            "/api/v1/departments",
            json={"name": "eng"},
        )
        test_client.post(
            "/api/v1/agents",
            json={
                "name": "carol",
                "role": "dev",
                "department": "eng",
                "level": "mid",
            },
        )
        # No If-Match header -- should succeed
        resp = test_client.patch(
            "/api/v1/agents/carol",
            json={"level": "senior"},
        )
        assert resp.status_code == 200
