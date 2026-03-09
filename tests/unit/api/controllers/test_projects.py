"""Tests for project controller."""

from typing import Any

import pytest
from litestar.testing import TestClient  # noqa: TC002


@pytest.mark.unit
class TestProjectController:
    def test_list_projects_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/projects")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    def test_get_project_stub(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/projects/any-id")
        assert resp.status_code == 501
        body = resp.json()
        assert body["success"] is False
        assert "not implemented" in body["error"].lower()
