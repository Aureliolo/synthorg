"""Tests for company controller."""

from typing import Any

import pytest
from litestar.testing import TestClient  # noqa: TC002


@pytest.mark.unit
class TestCompanyController:
    def test_get_company(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/company")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["company_name"] == "test-company"

    def test_list_departments(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/company/departments")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)
