"""Tests for the reports controller."""

import pytest

from synthorg.budget.state import BudgetStateSlice
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

_HEADERS = make_auth_headers("ceo")


@pytest.mark.unit
class TestReportsController:
    """Regression coverage for ``POST /api/v1/reports/generate``.

    The controller historically dereferenced ``state._app_state``
    (a private attribute that does not exist on Litestar's
    ``State``) so the endpoint surfaced a bare ``AttributeError``
    to clients; access now goes through ``state.app_state`` and the
    test wires ``AutomatedReportService`` on AppState so the
    endpoint serves the documented inputs instead of returning 503
    unconfigured.
    """

    async def test_generate_daily_report_succeeds(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
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

    async def test_list_periods(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get("/api/v1/reports/periods", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert {"daily", "weekly", "monthly"}.issubset(set(body["data"]))

    async def test_generate_rejects_unknown_period(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/reports/generate",
            headers=_HEADERS,
            json={"period": "fortnightly"},
        )
        # Pydantic enum validation fails before the service runs.
        assert resp.status_code == 400

    async def test_generate_returns_503_when_service_not_wired(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Regression guard: missing wiring -> 503, not AttributeError.

        The controller resolves the report service through
        ``state.slice(BudgetStateSlice).report_service`` -- when the
        slice field is ``None`` it raises ``ServiceUnavailableError``
        (HTTP 503), the honest status code for "feature unavailable in
        this deployment", rather than surfacing a bare ``AttributeError``
        as a 500. Force the not-wired state by swapping the budget slice's
        ``report_service`` to ``None``.
        """
        app_state = async_test_client.app.state.app_state
        original_slice = app_state.slice(BudgetStateSlice)
        app_state.wire(BudgetStateSlice, report_service=None)
        try:
            resp = await async_test_client.post(
                "/api/v1/reports/generate",
                headers=_HEADERS,
                json={"period": "daily"},
            )
            # 503 (NOT 500 AttributeError) when service is absent.
            assert resp.status_code == 503, resp.text
            body = resp.json()
            assert body["success"] is False
            # ``ServiceUnavailableError.error_code`` is the
            # ``ErrorCode.SERVICE_UNAVAILABLE`` enum value (an int).
            # Verifying both the HTTP 503 and the structured error code
            # locks down the contract; see ``core.error_taxonomy``.
            from synthorg.core.error_taxonomy import (
                ErrorCode,
            )

            assert (
                body.get("error_detail", {}).get("error_code")
                == ErrorCode.SERVICE_UNAVAILABLE
            )
        finally:
            app_state.swap_slice(original_slice)
