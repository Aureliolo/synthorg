"""Tests for budget controller."""

from datetime import UTC, datetime
from typing import Any

import pytest
from litestar.testing import TestClient  # noqa: TC002

from ai_company.budget.cost_record import CostRecord
from ai_company.budget.tracker import CostTracker  # noqa: TC001


@pytest.mark.unit
class TestBudgetController:
    def test_get_budget_config(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/budget/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "total_monthly" in body["data"]

    def test_list_cost_records_empty(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/budget/records")
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
            cost_usd=0.01,
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        )
        await cost_tracker.record(record)
        resp = test_client.get("/api/v1/budget/records")
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
            cost_usd=0.05,
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        )
        await cost_tracker.record(record)
        resp = test_client.get("/api/v1/budget/agents/bob")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["agent_id"] == "bob"
        assert body["data"]["total_cost_usd"] == 0.05
