"""Integration tests: Postgres composite indexes for cost_records / decision_records."""

from datetime import UTC, datetime, timedelta

import pytest
from psycopg import sql

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import CurrencyCode
from synthorg.core.enums import DecisionOutcome, TaskType
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend

pytestmark = pytest.mark.integration


_AGENTS = tuple(f"agent-{i:02d}" for i in range(5))
_TASKS = tuple(f"task-{i:03d}" for i in range(20))
_BASE = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


async def _seed_tasks(backend: PostgresPersistenceBackend) -> None:
    for task_id in _TASKS:
        await backend.tasks.save(
            Task(
                id=NotBlankStr(task_id),
                title=NotBlankStr(task_id),
                description=NotBlankStr("perf-index fixture"),
                type=TaskType.DEVELOPMENT,
                project=NotBlankStr("proj-perf"),
                created_by=NotBlankStr("system"),
            ),
        )


async def _seed_cost_records(backend: PostgresPersistenceBackend, n: int) -> None:
    for i in range(n):
        await backend.cost_records.save(
            CostRecord(
                agent_id=NotBlankStr(_AGENTS[i % len(_AGENTS)]),
                task_id=NotBlankStr(_TASKS[i % len(_TASKS)]),
                provider=NotBlankStr("test-provider"),
                model=NotBlankStr("test-small-001"),
                input_tokens=10,
                output_tokens=20,
                cost=0.001,
                currency=CurrencyCode("USD"),
                timestamp=_BASE + timedelta(seconds=i),
            ),
        )


async def _explain_plan(
    backend: PostgresPersistenceBackend,
    query: sql.SQL,
    *params: object,
    table: str,
) -> str:
    pool = backend._pool
    assert pool is not None, "fixture must connect the backend before EXPLAIN"
    analyze_stmt = sql.SQL("ANALYZE {}").format(sql.Identifier(table))
    explain_stmt = sql.SQL("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {}").format(query)
    async with pool.connection() as conn:
        await conn.execute(analyze_stmt)
        async with conn.cursor() as cur:
            # Force index usage so the plan reflects what the planner would
            # pick under load; at <1k rows it may otherwise prefer seq scan.
            await cur.execute("SET LOCAL enable_seqscan = off")
            await cur.execute(explain_stmt, params)
            rows = await cur.fetchall()
            return "\n".join(str(row[0]) for row in rows)


class TestCostRecordsCompositeIndexes:
    async def test_agent_timestamp_index_used(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _seed_tasks(postgres_backend)
        await _seed_cost_records(postgres_backend, n=200)
        plan = await _explain_plan(
            postgres_backend,
            sql.SQL(
                "SELECT * FROM cost_records WHERE agent_id = %s "
                "ORDER BY timestamp DESC LIMIT 50",
            ),
            _AGENTS[0],
            table="cost_records",
        )
        assert "idx_cost_records_agent_timestamp" in plan, (
            f"Composite (agent_id, timestamp DESC) index not used:\n{plan}"
        )

    async def test_task_timestamp_index_used(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _seed_tasks(postgres_backend)
        await _seed_cost_records(postgres_backend, n=200)
        plan = await _explain_plan(
            postgres_backend,
            sql.SQL(
                "SELECT * FROM cost_records WHERE task_id = %s "
                "ORDER BY timestamp DESC LIMIT 50",
            ),
            _TASKS[0],
            table="cost_records",
        )
        assert "idx_cost_records_task_timestamp" in plan, (
            f"Composite (task_id, timestamp DESC) index not used:\n{plan}"
        )


class TestDecisionRecordsCompositeIndex:
    async def test_task_recorded_id_index_used(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _seed_tasks(postgres_backend)
        for i, task_id in enumerate(_TASKS):
            await postgres_backend.decision_records.append_with_next_version(
                record_id=NotBlankStr(f"dec-{i:03d}"),
                task_id=NotBlankStr(task_id),
                approval_id=None,
                executing_agent_id=NotBlankStr(_AGENTS[0]),
                reviewer_agent_id=NotBlankStr(_AGENTS[1]),
                decision=DecisionOutcome.APPROVED,
                reason=None,
                criteria_snapshot=(),
                recorded_at=_BASE + timedelta(seconds=i),
            )
        plan = await _explain_plan(
            postgres_backend,
            sql.SQL(
                "SELECT * FROM decision_records WHERE task_id = %s "
                "ORDER BY recorded_at ASC, id ASC LIMIT 50",
            ),
            _TASKS[0],
            table="decision_records",
        )
        assert "idx_dr_task_recorded_id" in plan, (
            f"Composite (task_id, recorded_at, id) index not used:\n{plan}"
        )
