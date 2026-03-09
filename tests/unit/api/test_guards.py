"""Tests for route guards."""

from typing import Any

import pytest
from litestar.testing import TestClient  # noqa: TC002


@pytest.mark.unit
class TestGuards:
    def test_write_guard_allows_ceo(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test",
                "description": "Test desc",
                "type": "development",
                "project": "proj",
                "created_by": "alice",
            },
            headers={"X-Human-Role": "ceo"},
        )
        assert response.status_code == 201

    def test_write_guard_blocks_observer(self, test_client: TestClient[Any]) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test",
                "description": "Test desc",
                "type": "development",
                "project": "proj",
                "created_by": "alice",
            },
            headers={"X-Human-Role": "observer"},
        )
        assert response.status_code == 403

    def test_write_guard_blocks_missing_role(
        self, test_client: TestClient[Any]
    ) -> None:
        response = test_client.post(
            "/api/v1/tasks",
            json={
                "title": "Test",
                "description": "Test desc",
                "type": "development",
                "project": "proj",
                "created_by": "alice",
            },
        )
        assert response.status_code == 403
