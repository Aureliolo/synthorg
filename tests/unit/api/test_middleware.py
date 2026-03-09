"""Tests for request logging middleware."""

from typing import Any

import pytest
from litestar.testing import TestClient  # noqa: TC002


@pytest.mark.unit
class TestRequestLoggingMiddleware:
    def test_request_completes_with_status(self, test_client: TestClient[Any]) -> None:
        response = test_client.get("/api/v1/health")
        assert response.status_code == 200

    def test_not_found_returns_correct_status(
        self, test_client: TestClient[Any]
    ) -> None:
        response = test_client.get("/api/v1/agents/nonexistent")
        assert response.status_code == 404
