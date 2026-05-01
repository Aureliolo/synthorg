"""Tests for coordination metrics query controller."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.budget.coordination_metrics import (
    CoordinationMetrics,
    MessageOverhead,
)
from synthorg.budget.coordination_store import (
    CoordinationMetricsRecord,
    CoordinationMetricsStore,
)
from tests.unit.api.conftest import make_auth_headers

_HEADERS = make_auth_headers("ceo")


def _make_record(
    *,
    task_id: str = "task-1",
    agent_id: str | None = "agent-a",
    timestamp: datetime | None = None,
    team_size: int = 3,
    message_overhead: MessageOverhead | None = None,
) -> CoordinationMetricsRecord:
    metrics = CoordinationMetrics(
        message_overhead=message_overhead,
    )
    return CoordinationMetricsRecord(
        task_id=task_id,
        agent_id=agent_id,
        computed_at=timestamp or datetime(2026, 4, 1, tzinfo=UTC),
        team_size=team_size,
        metrics=metrics,
    )


@pytest.mark.unit
class TestCoordinationMetricsController:
    def test_empty_store(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.get(
            "/api/v1/coordination/metrics",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []

    def test_returns_records(
        self,
        test_client: TestClient[Any],
        coordination_metrics_store: CoordinationMetricsStore,
    ) -> None:
        coordination_metrics_store.record(_make_record())
        coordination_metrics_store.record(
            _make_record(task_id="task-2"),
        )
        resp = test_client.get(
            "/api/v1/coordination/metrics",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        resp.json()

    def test_filter_by_task_id(
        self,
        test_client: TestClient[Any],
        coordination_metrics_store: CoordinationMetricsStore,
    ) -> None:
        coordination_metrics_store.record(
            _make_record(task_id="task-1"),
        )
        coordination_metrics_store.record(
            _make_record(task_id="task-2"),
        )
        resp = test_client.get(
            "/api/v1/coordination/metrics",
            params={"task_id": "task-1"},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["task_id"] == "task-1"

    def test_filter_by_agent_id(
        self,
        test_client: TestClient[Any],
        coordination_metrics_store: CoordinationMetricsStore,
    ) -> None:
        coordination_metrics_store.record(
            _make_record(agent_id="alice"),
        )
        coordination_metrics_store.record(
            _make_record(agent_id="bob"),
        )
        resp = test_client.get(
            "/api/v1/coordination/metrics",
            params={"agent_id": "alice"},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        resp.json()

    def test_filter_by_time_range(
        self,
        test_client: TestClient[Any],
        coordination_metrics_store: CoordinationMetricsStore,
    ) -> None:
        t1 = datetime(2026, 4, 1, tzinfo=UTC)
        t2 = t1 + timedelta(hours=1)
        t3 = t1 + timedelta(hours=2)
        coordination_metrics_store.record(
            _make_record(timestamp=t1),
        )
        coordination_metrics_store.record(
            _make_record(timestamp=t2, task_id="task-2"),
        )
        coordination_metrics_store.record(
            _make_record(timestamp=t3, task_id="task-3"),
        )
        resp = test_client.get(
            "/api/v1/coordination/metrics",
            params={
                "since": t1.isoformat(),
                "until": t2.isoformat(),
            },
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        resp.json()

    def test_pagination(
        self,
        test_client: TestClient[Any],
        coordination_metrics_store: CoordinationMetricsStore,
    ) -> None:
        for i in range(5):
            coordination_metrics_store.record(
                _make_record(task_id=f"task-{i}"),
            )
        # Walk one page, then use the returned cursor to advance.
        resp1 = test_client.get(
            "/api/v1/coordination/metrics",
            params={"limit": 1},
            headers=_HEADERS,
        )
        assert resp1.status_code == 200
        cursor = resp1.json()["pagination"]["next_cursor"]
        assert cursor is not None
        resp = test_client.get(
            "/api/v1/coordination/metrics",
            params={"limit": 2, "cursor": cursor},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["limit"] == 2
        assert len(body["data"]) == 2

    def test_message_overhead_is_quadratic_surfaced(
        self,
        test_client: TestClient[Any],
        coordination_metrics_store: CoordinationMetricsStore,
    ) -> None:
        overhead = MessageOverhead(
            team_size=5,
            message_count=20,
            quadratic_threshold=0.5,
        )
        coordination_metrics_store.record(
            _make_record(message_overhead=overhead),
        )
        resp = test_client.get(
            "/api/v1/coordination/metrics",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        msg_oh = body["data"][0]["metrics"]["message_overhead"]
        assert msg_oh["is_quadratic"] is True

    def test_combined_filters_and(
        self,
        test_client: TestClient[Any],
        coordination_metrics_store: CoordinationMetricsStore,
    ) -> None:
        """Multiple filters are AND-combined."""
        t1 = datetime(2026, 4, 1, tzinfo=UTC)
        t2 = t1 + timedelta(hours=1)
        coordination_metrics_store.record(
            _make_record(task_id="t1", agent_id="alice", timestamp=t1),
        )
        coordination_metrics_store.record(
            _make_record(task_id="t2", agent_id="alice", timestamp=t2),
        )
        coordination_metrics_store.record(
            _make_record(task_id="t3", agent_id="bob", timestamp=t1),
        )
        resp = test_client.get(
            "/api/v1/coordination/metrics",
            params={"agent_id": "alice", "since": t1.isoformat()},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        resp.json()

    def test_rejects_inverted_time_window(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """since > until is a validation failure (HTTP 422)."""
        t1 = datetime(2026, 4, 1, tzinfo=UTC)
        t2 = t1 - timedelta(hours=1)
        resp = test_client.get(
            "/api/v1/coordination/metrics",
            params={"since": t1.isoformat(), "until": t2.isoformat()},
            headers=_HEADERS,
        )
        assert resp.status_code == 422
