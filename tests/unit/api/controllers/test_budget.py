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
            cost_usd=0.01,
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
            cost_usd=0.05,
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        )
        await cost_tracker.record(record)
        resp = test_client.get("/api/v1/budget/agents/bob", headers=_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["agent_id"] == "bob"
        assert body["data"]["total_cost_usd"] == 0.05

    def test_budget_requires_read_access(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get(
            "/api/v1/budget/config",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401
