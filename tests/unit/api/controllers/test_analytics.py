"""Tests for analytics controller."""

from typing import Any

import pytest
from litestar.testing import TestClient  # noqa: TC002


@pytest.mark.unit
class TestAnalyticsController:
    def test_overview_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/analytics/overview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["total_tasks"] == 0
        assert data["tasks_by_status"] == {}
        assert data["total_agents"] == 0
        assert data["total_cost_usd"] == 0.0
