"""Tests for project controller."""

from typing import Any

import pytest
import structlog.testing
from litestar.testing import TestClient

from tests.unit.api.conftest import make_auth_headers


@pytest.mark.unit
class TestProjectController:
    def test_list_projects_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/projects")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    def test_get_project_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/projects/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()

    def test_create_and_get_project(self, test_client: TestClient[Any]) -> None:
        create_resp = test_client.post(
            "/api/v1/projects",
            json={
                "name": "Auth System",
                "description": "Build authentication",
                "budget": 500.0,
            },
            headers=make_auth_headers("ceo"),
        )
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["success"] is True
        project_id = created["data"]["id"]
        assert project_id.startswith("proj-")
        assert created["data"]["name"] == "Auth System"
        assert created["data"]["budget"] == 500.0

        get_resp = test_client.get(f"/api/v1/projects/{project_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["id"] == project_id

    def test_list_projects_after_create(self, test_client: TestClient[Any]) -> None:
        test_client.post(
            "/api/v1/projects",
            json={"name": "P1"},
            headers=make_auth_headers("ceo"),
        )
        test_client.post(
            "/api/v1/projects",
            json={"name": "P2"},
            headers=make_auth_headers("ceo"),
        )
        resp = test_client.get("/api/v1/projects")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2

    def test_list_projects_has_more_with_overflow(
        self, test_client: TestClient[Any]
    ) -> None:
        for i in range(4):
            test_client.post(
                "/api/v1/projects",
                json={"name": f"P-page-{i:02d}"},
                headers=make_auth_headers("ceo"),
            )

        resp = test_client.get("/api/v1/projects?limit=2")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) <= 2
        assert body["pagination"]["has_more"] is True
        assert body["pagination"]["next_cursor"] is not None

    def test_create_project_with_deadline(self, test_client: TestClient[Any]) -> None:
        resp = test_client.post(
            "/api/v1/projects",
            json={
                "name": "Deadline Project",
                "deadline": "2026-12-31",
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["deadline"] == "2026-12-31"

    def test_create_project_invalid_deadline(
        self, test_client: TestClient[Any]
    ) -> None:
        resp = test_client.post(
            "/api/v1/projects",
            json={
                "name": "Bad Deadline",
                "deadline": "not-a-date",
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    def test_oversized_project_id_rejected(self, test_client: TestClient[Any]) -> None:
        long_id = "x" * 129
        resp = test_client.get(f"/api/v1/projects/{long_id}")
        assert resp.status_code == 400

    def test_list_projects_filter_by_invalid_status(
        self, test_client: TestClient[Any]
    ) -> None:
        resp = test_client.get("/api/v1/projects?status=bogus")
        # ValidationError is 422 Unprocessable Entity (RFC 9457).
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert "Invalid project status" in body["error"]
        assert body["error_detail"]["error_category"] == "validation"

    def test_create_project_with_duplicate_team(
        self, test_client: TestClient[Any]
    ) -> None:
        resp = test_client.post(
            "/api/v1/projects",
            json={
                "name": "Dupe Team",
                "team": ["agent-1", "agent-1"],
            },
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 400

    def test_delete_project_succeeds(self, test_client: TestClient[Any]) -> None:
        create_resp = test_client.post(
            "/api/v1/projects",
            json={"name": "To be deleted"},
            headers=make_auth_headers("ceo"),
        )
        assert create_resp.status_code == 201
        project_id = create_resp.json()["data"]["id"]

        delete_resp = test_client.delete(
            f"/api/v1/projects/{project_id}",
            headers=make_auth_headers("ceo"),
        )
        assert delete_resp.status_code == 204

        # Subsequent fetch must 404.
        get_resp = test_client.get(f"/api/v1/projects/{project_id}")
        assert get_resp.status_code == 404

    def test_delete_project_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.delete(
            "/api/v1/projects/proj-does-not-exist",
            headers=make_auth_headers("ceo"),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "Project 'proj-does-not-exist' not found"

    def test_delete_project_broadcasts_ws_event(
        self,
        test_client: TestClient[Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Successful delete must publish a PROJECT_DELETED WS event.

        Regression guard: the controller's WS broadcast is easy to drop during
        refactors because it is fire-and-forget and silent on failure.
        """
        captured: list[dict[str, Any]] = []

        def capture(
            request: Any,
            event_type: Any,
            channel: str,
            payload: dict[str, Any],
        ) -> None:
            captured.append(
                {
                    "event_type": event_type,
                    "channel": channel,
                    "payload": payload,
                },
            )

        # String-path form so the module attribute is patched by name; the
        # underlying channels.publish_ws_event is still exercised on other
        # endpoints that do not go through this test.
        monkeypatch.setattr(
            "synthorg.api.controllers.projects.publish_ws_event",
            capture,
        )

        create_resp = test_client.post(
            "/api/v1/projects",
            json={"name": "Doomed"},
            headers=make_auth_headers("ceo"),
        )
        project_id = create_resp.json()["data"]["id"]

        with structlog.testing.capture_logs() as logs:
            delete_resp = test_client.delete(
                f"/api/v1/projects/{project_id}",
                headers=make_auth_headers("ceo"),
            )
        assert delete_resp.status_code == 204

        delete_events = [
            call
            for call in captured
            if getattr(call["event_type"], "value", call["event_type"])
            == "project.deleted"
        ]
        assert len(delete_events) == 1
        event = delete_events[0]
        assert event["channel"] == "projects"
        assert event["payload"]["project_id"] == project_id
        assert event["payload"]["name"] == "Doomed"

        # Audit-trail regression guard: ProjectService.delete() must
        # emit ``api.project.deleted`` with the project_id at INFO.  If
        # the audit is silently dropped during a refactor, monitoring
        # filters that key on the event name will go quiet.
        deleted_audit = [log for log in logs if log["event"] == "api.project.deleted"]
        assert len(deleted_audit) >= 1, (
            f"expected api.project.deleted audit log; got events: "
            f"{[log['event'] for log in logs]}"
        )
        assert deleted_audit[0]["project_id"] == project_id
