"""Tests for department controller."""

from typing import Any

import pytest
from litestar.testing import TestClient  # noqa: TC002


@pytest.mark.unit
class TestDepartmentController:
    def test_list_departments_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/departments")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    def test_get_department_not_found(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/departments/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["success"] is False
