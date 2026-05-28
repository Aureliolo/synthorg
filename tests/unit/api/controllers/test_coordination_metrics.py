"""Tests for coordination metrics query controller."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.coordination_metrics import (
    CoordinationMetrics,
    MessageOverhead,
)
from synthorg.budget.coordination_store import (
    CoordinationMetricsRecord,
    CoordinationMetricsStore,
)
from synthorg.persistence._shared import parse_iso_utc
from tests._shared import LoopAsyncClient
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
    async def test_empty_store(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get(
            "/api/v1/coordination/metrics",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == []

    async def test_returns_records(
        self,
        async_test_client: LoopAsyncClient,
        coordination_metrics_store: CoordinationMetricsStore,
    ) -> None:
        coordination_metrics_store.record(_make_record())
        coordination_metrics_store.record(
            _make_record(task_id="task-2"),
        )
        resp = await async_test_client.get(
            "/api/v1/coordination/metrics",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Both records show up unfiltered.  Assert exact cardinality
        # AND the exact seeded task_id set so a regression that
        # duplicates or drops rows still fails the test (a set-only
        # assertion would silently accept duplicates).
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 2
        assert {row["task_id"] for row in body["data"]} == {"task-1", "task-2"}

    async def test_filter_by_task_id(
        self,
        async_test_client: LoopAsyncClient,
        coordination_metrics_store: CoordinationMetricsStore,
    ) -> None:
        coordination_metrics_store.record(
            _make_record(task_id="task-1"),
        )
        coordination_metrics_store.record(
            _make_record(task_id="task-2"),
        )
        resp = await async_test_client.get(
            "/api/v1/coordination/metrics",
            params={"task_id": "task-1"},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"][0]["task_id"] == "task-1"

    async def test_filter_by_agent_id(
        self,
        async_test_client: LoopAsyncClient,
        coordination_metrics_store: CoordinationMetricsStore,
    ) -> None:
        coordination_metrics_store.record(
            _make_record(agent_id="alice"),
        )
        coordination_metrics_store.record(
            _make_record(agent_id="bob"),
        )
        resp = await async_test_client.get(
            "/api/v1/coordination/metrics",
            params={"agent_id": "alice"},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Filter is honoured: exactly one ``alice`` record was seeded so
        # the response must carry exactly one row.  ``len >= 1`` would
        # silently pass if the endpoint started duplicating rows or
        # returning extra ``alice`` records.
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 1
        assert all(row.get("agent_id") == "alice" for row in body["data"])

    async def test_filter_by_time_range(
        self,
        async_test_client: LoopAsyncClient,
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
        resp = await async_test_client.get(
            "/api/v1/coordination/metrics",
            params={
                "since": t1.isoformat(),
                "until": t2.isoformat(),
            },
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Time window includes t1 and t2 but excludes t3.  Assert the
        # exact in-window cardinality and id set so a regression that
        # drops every row still fails this test (a set-only assertion
        # would also silently accept duplicates).
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 2
        task_ids = {row["task_id"] for row in body["data"]}
        assert task_ids == {"task-1", "task-2"}

    async def test_pagination(
        self,
        async_test_client: LoopAsyncClient,
        coordination_metrics_store: CoordinationMetricsStore,
    ) -> None:
        for i in range(5):
            coordination_metrics_store.record(
                _make_record(task_id=f"task-{i}"),
            )
        # Walk one page, then use the returned cursor to advance.
        resp1 = await async_test_client.get(
            "/api/v1/coordination/metrics",
            params={"limit": 1},
            headers=_HEADERS,
        )
        assert resp1.status_code == 200
        body1 = resp1.json()
        # Explicit row count + set-size equality both gate the page:
        # the row-count check catches a server that returns an empty
        # body when one row was expected, and ``len(set) == len(data)``
        # catches duplicated rows on a single page (a set-only check
        # silently collapses duplicates).
        assert len(body1["data"]) == 1
        cursor = body1["pagination"]["next_cursor"]
        assert cursor is not None
        page1_task_ids = {row["task_id"] for row in body1["data"]}
        assert len(page1_task_ids) == len(body1["data"])
        resp = await async_test_client.get(
            "/api/v1/coordination/metrics",
            params={"limit": 2, "cursor": cursor},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["limit"] == 2
        assert len(body["data"]) == 2
        # Cursor-only contract: the second page must be disjoint from
        # the first AND its rows must be unique within the page (a
        # backend that replays the first page would fail the disjoint
        # check; one that returns ``[same, same]`` would fail the
        # uniqueness check).
        page2_task_ids = {row["task_id"] for row in body["data"]}
        assert len(page2_task_ids) == len(body["data"])
        assert page1_task_ids.isdisjoint(page2_task_ids)

    async def test_message_overhead_is_quadratic_surfaced(
        self,
        async_test_client: LoopAsyncClient,
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
        resp = await async_test_client.get(
            "/api/v1/coordination/metrics",
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        msg_oh = body["data"][0]["metrics"]["message_overhead"]
        assert msg_oh["is_quadratic"] is True

    async def test_combined_filters_and(
        self,
        async_test_client: LoopAsyncClient,
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
        resp = await async_test_client.get(
            "/api/v1/coordination/metrics",
            params={"agent_id": "alice", "since": t1.isoformat()},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        # Combined filter (agent + since) honoured: only alice's
        # records, none from before t1.
        assert isinstance(body["data"], list)
        # Assert exact cardinality plus the seeded id set so an empty
        # body cannot pass the per-row checks vacuously and a duplicate
        # row cannot pass the set-only check.
        assert len(body["data"]) == 2
        task_ids = {row["task_id"] for row in body["data"]}
        assert task_ids == {"t1", "t2"}
        assert all(row.get("agent_id") == "alice" for row in body["data"])
        # Assert ``computed_at`` (the wire field for ``timestamp``) is
        # present on every row -- a missing field would otherwise be
        # silently masked by a defaulted ``row.get(...)``.  Parse via
        # ``parse_iso_utc`` so the strict ISO-UTC contract used by the
        # persistence layer also gates the API surface here.
        for row in body["data"]:
            assert "computed_at" in row
            assert parse_iso_utc(row["computed_at"]) >= t1

    async def test_rejects_inverted_time_window(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """since > until is a validation failure (HTTP 422)."""
        t1 = datetime(2026, 4, 1, tzinfo=UTC)
        t2 = t1 - timedelta(hours=1)
        resp = await async_test_client.get(
            "/api/v1/coordination/metrics",
            params={"since": t1.isoformat(), "until": t2.isoformat()},
            headers=_HEADERS,
        )
        assert resp.status_code == 422
