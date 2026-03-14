"""Tests for analytics controller."""

from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.core.enums import TaskStatus
from tests.unit.api.conftest import make_auth_headers

_HEADERS = make_auth_headers("ceo")


@pytest.mark.unit
class TestAnalyticsController:
    def test_overview_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/analytics/overview", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["total_tasks"] == 0
        expected_statuses = {s.value: 0 for s in TaskStatus}
        assert data["tasks_by_status"] == expected_statuses
        assert data["total_agents"] == 0
        assert data["total_cost_usd"] == 0.0

    def test_overview_requires_read_access(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get(
            "/api/v1/analytics/overview",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401
