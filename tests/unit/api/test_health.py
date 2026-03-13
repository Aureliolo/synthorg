"""Tests for health check endpoint."""

from typing import Any

import pytest
from litestar.testing import TestClient


@pytest.mark.unit
class TestHealthCheck:
    def test_returns_ok_when_all_healthy(self, test_client: TestClient[Any]) -> None:
        response = test_client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["status"] == "ok"
        assert body["data"]["persistence"] is True
        assert body["data"]["message_bus"] is True
        assert "version" in body["data"]
        assert body["data"]["uptime_seconds"] >= 0

    def test_reports_degraded_when_bus_down(
        self,
        test_client: TestClient[Any],
        fake_message_bus: Any,
    ) -> None:
        fake_message_bus._running = False
        response = test_client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "degraded"
        assert body["data"]["message_bus"] is False

    def test_reports_down_when_all_unhealthy(
        self,
        test_client: TestClient[Any],
        fake_persistence: Any,
        fake_message_bus: Any,
    ) -> None:
        fake_persistence._connected = False
        fake_message_bus._running = False
        response = test_client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["status"] == "down"
