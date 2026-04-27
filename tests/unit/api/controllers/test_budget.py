"""Tests for budget controller."""

from datetime import UTC, datetime
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from tests.unit.api.conftest import make_auth_headers

_HEADERS = make_auth_headers("ceo")


@pytest.mark.unit
class TestBudgetController:
    def test_get_budget_config(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/budget/config", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "total_monthly" in body["data"]

    def test_list_cost_records_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/budget/records", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    async def test_list_cost_records_with_data(
        self,
        test_client: TestClient[Any],
        cost_tracker: CostTracker,
    ) -> None:
        record = CostRecord(
            agent_id="alice",
            task_id="task-1",
            provider="test-provider",
            model="test-model-001",
            input_tokens=100,
            output_tokens=50,
            cost=0.01,
            currency="USD",
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        )
        await cost_tracker.record(record)
        resp = test_client.get("/api/v1/budget/records", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total"] == 1

    async def test_agent_spending(
        self,
        test_client: TestClient[Any],
        cost_tracker: CostTracker,
    ) -> None:
        record = CostRecord(
            agent_id="bob",
            task_id="task-1",
            provider="test-provider",
            model="test-model-001",
            input_tokens=100,
            output_tokens=50,
            cost=0.05,
            currency="USD",
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        )
        await cost_tracker.record(record)
        resp = test_client.get("/api/v1/budget/agents/bob", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["agent_id"] == "bob"
        assert body["data"]["total_cost"] == 0.05

    def test_budget_requires_read_access(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get(
            "/api/v1/budget/config",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401

    def test_oversized_agent_id_rejected(self, test_client: TestClient[Any]) -> None:
        long_id = "x" * 129
        resp = test_client.get(
            f"/api/v1/budget/agents/{long_id}",
            headers=_HEADERS,
        )
        assert resp.status_code == 400


@pytest.mark.unit
class TestBudgetSummaries:
    """Tests for daily_summary and period_summary computed fields."""

    def test_list_includes_daily_summary(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/budget/records", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "daily_summary" in body
        assert "period_summary" in body

    async def test_daily_summary_groups_by_date(
        self,
        test_client: TestClient[Any],
        cost_tracker: CostTracker,
    ) -> None:
        for day in (1, 1, 2):
            await cost_tracker.record(
                CostRecord(
                    agent_id="alice",
                    task_id="task-1",
                    provider="test-provider",
                    model="test-model-001",
                    input_tokens=100,
                    output_tokens=50,
                    cost=0.01,
                    currency="USD",
                    timestamp=datetime(2026, 3, day, tzinfo=UTC),
                ),
            )
        resp = test_client.get("/api/v1/budget/records", headers=_HEADERS)
        body = resp.json()
        daily = body["daily_summary"]
        assert len(daily) == 2
        dates = [d["date"] for d in daily]
        assert "2026-03-01" in dates
        assert "2026-03-02" in dates
        # Verify chronological sort order
        assert dates == sorted(dates)
        # Day 1 has 2 records with known aggregates
        day1 = next(d for d in daily if d["date"] == "2026-03-01")
        assert day1["record_count"] == 2
        assert day1["total_cost"] == pytest.approx(0.02)
        assert day1["total_input_tokens"] == 200
        assert day1["total_output_tokens"] == 100

    async def test_period_summary_avg_cost(
        self,
        test_client: TestClient[Any],
        cost_tracker: CostTracker,
    ) -> None:
        for cost in (0.10, 0.20, 0.30):
            await cost_tracker.record(
                CostRecord(
                    agent_id="alice",
                    task_id="task-1",
                    provider="test-provider",
                    model="test-model-001",
                    input_tokens=100,
                    output_tokens=50,
                    cost=cost,
                    currency="USD",
                    timestamp=datetime(2026, 3, 1, tzinfo=UTC),
                ),
            )
        resp = test_client.get("/api/v1/budget/records", headers=_HEADERS)
        body = resp.json()
        period = body["period_summary"]
        assert period["record_count"] == 3
        assert period["total_cost"] == pytest.approx(0.60)
        assert period["avg_cost"] == pytest.approx(0.20)
        assert period["total_input_tokens"] == 300
        assert period["total_output_tokens"] == 150

    def test_period_summary_empty(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get("/api/v1/budget/records", headers=_HEADERS)
        body = resp.json()
        period = body["period_summary"]
        assert period["record_count"] == 0
        assert period["total_cost"] == 0.0
        assert period["avg_cost"] == 0.0

    async def test_summaries_from_all_records_not_page(
        self,
        test_client: TestClient[Any],
        cost_tracker: CostTracker,
    ) -> None:
        for i in range(3):
            await cost_tracker.record(
                CostRecord(
                    agent_id="alice",
                    task_id=f"task-{i}",
                    provider="test-provider",
                    model="test-model-001",
                    input_tokens=100,
                    output_tokens=50,
                    cost=0.10,
                    currency="USD",
                    timestamp=datetime(2026, 3, 1, tzinfo=UTC),
                ),
            )
        resp = test_client.get(
            "/api/v1/budget/records",
            params={"limit": 1},
            headers=_HEADERS,
        )
        body = resp.json()
        # Page has 1 record but summaries cover all 3
        assert len(body["data"]) == 1
        assert body["period_summary"]["record_count"] == 3
        assert body["period_summary"]["total_cost"] == pytest.approx(0.30)

    async def test_summaries_respect_agent_filter(
        self,
        test_client: TestClient[Any],
        cost_tracker: CostTracker,
    ) -> None:
        for agent in ("alice", "alice", "bob"):
            await cost_tracker.record(
                CostRecord(
                    agent_id=agent,
                    task_id="task-1",
                    provider="test-provider",
                    model="test-model-001",
                    input_tokens=100,
                    output_tokens=50,
                    cost=0.10,
                    currency="USD",
                    timestamp=datetime(2026, 3, 1, tzinfo=UTC),
                ),
            )
        resp = test_client.get(
            "/api/v1/budget/records",
            params={"agent_id": "alice", "limit": 1},
            headers=_HEADERS,
        )
        body = resp.json()
        # Page has 1 record, summaries cover 2 (alice only, not bob)
        assert len(body["data"]) == 1
        assert body["period_summary"]["record_count"] == 2
        assert body["period_summary"]["total_cost"] == pytest.approx(0.20)
        assert body["period_summary"]["total_input_tokens"] == 200
        assert body["period_summary"]["total_output_tokens"] == 100


@pytest.mark.unit
class TestBuildSummariesCurrencyInvariant:
    """Same-currency invariant in ``_build_summaries``.

    Audit finding 126: cost summation across currencies is meaningless
    without an FX policy and must be rejected at the aggregator
    boundary.  ``_build_summaries`` raises
    ``MixedCurrencyAggregationError`` when any record's currency
    differs from the requested ``currency`` (or when records span
    multiple currencies).
    """

    def _make(
        self,
        *,
        currency: str = "USD",
        cost: float = 0.10,
    ) -> CostRecord:
        return CostRecord(
            agent_id="alice",
            task_id="task-1",
            provider="test-provider",
            model="test-model-001",
            input_tokens=100,
            output_tokens=50,
            cost=cost,
            currency=currency,
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        )

    def test_same_currency_accepted(self) -> None:
        from synthorg.api.controllers.budget import _build_summaries

        records = (self._make(currency="USD"), self._make(currency="USD"))
        daily, period = _build_summaries(records, currency="USD")
        assert period.record_count == 2
        assert period.currency == "USD"
        assert len(daily) == 1

    @pytest.mark.parametrize(
        ("record_currencies", "summary_currency"),
        [
            # Two records spanning different currencies.
            pytest.param(("USD", "EUR"), "USD", id="multi-currency-records"),
            # Single record whose currency does not match the
            # caller-requested summary currency.
            pytest.param(("EUR",), "USD", id="record-currency-mismatch"),
        ],
    )
    def test_mixed_currency_raises(
        self,
        record_currencies: tuple[str, ...],
        summary_currency: str,
    ) -> None:
        from synthorg.api.controllers.budget import _build_summaries
        from synthorg.budget.errors import MixedCurrencyAggregationError

        records = tuple(self._make(currency=cur) for cur in record_currencies)
        with pytest.raises(MixedCurrencyAggregationError):
            _build_summaries(records, currency=summary_currency)

    def test_empty_records_no_invariant_check(self) -> None:
        from synthorg.api.controllers.budget import _build_summaries

        daily, period = _build_summaries((), currency="USD")
        assert daily == ()
        assert period.record_count == 0
        assert period.currency == "USD"


@pytest.mark.unit
class TestCostRecordListResponseValidator:
    """Tests for CostRecordListResponse error/error_detail consistency."""

    def test_error_without_error_detail_raises(self) -> None:
        from synthorg.api.controllers.budget import (
            CostRecordListResponse,
            PeriodSummary,
        )
        from synthorg.api.dto import PaginationMeta

        msg = "error must be accompanied by error_detail"
        with pytest.raises(ValueError, match=msg):
            CostRecordListResponse(
                error="something went wrong",
                pagination=PaginationMeta(limit=50, next_cursor=None, has_more=False),
                period_summary=PeriodSummary(
                    total_cost=0.0,
                    total_input_tokens=0,
                    total_output_tokens=0,
                    record_count=0,
                ),
            )

    def test_error_detail_without_error_raises(self) -> None:
        from synthorg.api.controllers.budget import (
            CostRecordListResponse,
            PeriodSummary,
        )
        from synthorg.api.dto import ErrorDetail, PaginationMeta
        from synthorg.api.errors import ErrorCategory, ErrorCode

        detail = ErrorDetail(
            detail="test detail",
            error_code=ErrorCode.VALIDATION_ERROR,
            error_category=ErrorCategory.VALIDATION,
            instance="req-001",
            title="Test",
            type="about:blank",
        )
        msg = "error_detail requires error to be set"
        with pytest.raises(ValueError, match=msg):
            CostRecordListResponse(
                error_detail=detail,
                pagination=PaginationMeta(
                    limit=50,
                    next_cursor=None,
                    has_more=False,
                ),
                period_summary=PeriodSummary(
                    total_cost=0.0,
                    total_input_tokens=0,
                    total_output_tokens=0,
                    record_count=0,
                ),
            )

    def test_both_error_and_detail_accepted(self) -> None:
        from synthorg.api.controllers.budget import (
            CostRecordListResponse,
            PeriodSummary,
        )
        from synthorg.api.dto import ErrorDetail, PaginationMeta
        from synthorg.api.errors import ErrorCategory, ErrorCode

        resp = CostRecordListResponse(
            error="bad request",
            error_detail=ErrorDetail(
                detail="bad request detail",
                error_code=ErrorCode.VALIDATION_ERROR,
                error_category=ErrorCategory.VALIDATION,
                instance="req-002",
                title="Bad Request",
                type="about:blank",
            ),
            pagination=PaginationMeta(
                limit=50,
                next_cursor=None,
                has_more=False,
            ),
            period_summary=PeriodSummary(
                total_cost=0.0,
                total_input_tokens=0,
                total_output_tokens=0,
                record_count=0,
            ),
        )
        assert resp.success is False

    def test_success_true_when_no_error(self) -> None:
        from synthorg.api.controllers.budget import (
            CostRecordListResponse,
            PeriodSummary,
        )
        from synthorg.api.dto import PaginationMeta

        resp = CostRecordListResponse(
            pagination=PaginationMeta(limit=50, next_cursor=None, has_more=False),
            period_summary=PeriodSummary(
                total_cost=0.0,
                total_input_tokens=0,
                total_output_tokens=0,
                record_count=0,
            ),
        )
        assert resp.success is True
