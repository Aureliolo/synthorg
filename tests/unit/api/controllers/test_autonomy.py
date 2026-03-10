"""Tests for autonomy controller."""

from typing import Any

import pytest
from litestar.testing import TestClient  # noqa: TC002

from tests.unit.api.conftest import make_auth_headers

_BASE = "/api/v1/agents"
_WRITE_HEADERS = make_auth_headers("ceo")
_READ_HEADERS = make_auth_headers("observer")


def _url(agent_id: str = "agent-001") -> str:
    return f"{_BASE}/{agent_id}/autonomy"


@pytest.mark.unit
class TestGetAutonomy:
    def test_get_autonomy(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get(_url("agent-42"), headers=_READ_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["agent_id"] == "agent-42"
        assert data["level"] == "semi"
        assert data["promotion_pending"] is False

    def test_get_autonomy_requires_read_access(
        self, test_client: TestClient[Any]
    ) -> None:
        resp = test_client.get(
            _url(), headers={"Authorization": "Bearer invalid-token"}
        )
        assert resp.status_code == 401


@pytest.mark.unit
class TestUpdateAutonomy:
    def test_update_autonomy_returns_pending(
        self, test_client: TestClient[Any]
    ) -> None:
        resp = test_client.post(
            _url("agent-42"),
            json={"level": "full"},
            headers=_WRITE_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["agent_id"] == "agent-42"
        assert data["level"] == "semi"
        assert data["promotion_pending"] is True

    def test_update_autonomy_requires_write_access(
        self, test_client: TestClient[Any]
    ) -> None:
        resp = test_client.post(
            _url(),
            json={"level": "full"},
            headers=_READ_HEADERS,
        )
        assert resp.status_code == 403
