"""Tests for the reports controller (#1666 B-2 regression)."""

from typing import Any

import pytest
from litestar.testing import TestClient

from tests.unit.api.conftest import make_auth_headers

_HEADERS = make_auth_headers("ceo")


@pytest.mark.unit
class TestReportsController:
    """Regression coverage for ``POST /api/v1/reports/generate``.

    Pre-#1666 the controller dereferenced ``state._app_state``
    (private attribute that does not exist on Litestar's ``State``)
    so the endpoint surfaced a bare ``AttributeError`` to clients.
    The fix swaps the access to ``state.app_state`` and wires
    ``AutomatedReportService`` on AppState so the endpoint serves
    the documented inputs instead of returning 503 unconfigured.
    """

    def test_generate_daily_report_succeeds(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/reports/generate",
            headers=_HEADERS,
            json={"period": "daily"},
        )
        # Litestar defaults POST to 201 Created.
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["period"] == "daily"
        assert "start" in data
        assert "end" in data
        assert "generated_at" in data
        # All four sub-report flags are booleans -- the DTO
        # surfaces "is anything in this slot" rather than
        # leaking the inner aggregate shape.
        assert isinstance(data["has_spending"], bool)
        assert isinstance(data["has_performance"], bool)
        assert isinstance(data["has_task_completion"], bool)
        assert isinstance(data["has_risk_trends"], bool)

    def test_list_periods(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/reports/periods", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert {"daily", "weekly", "monthly"}.issubset(set(body["data"]))

    def test_generate_rejects_unknown_period(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/reports/generate",
            headers=_HEADERS,
            json={"period": "fortnightly"},
        )
        # Pydantic enum validation fails before the service runs.
        assert resp.status_code == 400
