"""Integration tests: SQLite composite indexes for cost_records / decision_records.

The EXPLAIN-asserting cases use a simpler ``ORDER BY`` than the
production repo query (which trails ``agent_id ASC, rowid ASC`` as
deterministic tiebreakers). With those tiebreakers SQLite at 200 rows
prefers the existing single-column ``idx_cost_records_*`` indexes plus
a temp-B-tree sort over the new composites. The composite index is
the right shape for cursor-pagination queries (single-column trailing
ORDER BY), which is why these tests assert hits against THAT shape.
The trade-off is intentional: the production query is unchanged, the
single-column indexes still serve it efficiently at the row counts we
target, and the new composites earn their keep on cursor-paginated
queries we are about to add. Re-run ``EXPLAIN`` against the production
ORDER BY if the planner choice shifts.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import CurrencyCode
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decisions import DecisionOutcome
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration


_AGENTS = tuple(f"agent-{i:02d}" for i in range(5))
_TASKS = tuple(f"task-{i:03d}" for i in range(20))
_BASE = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


async def _seed_tasks(backend: SQLitePersistenceBackend) -> None:
    """Seed the tasks rows that cost_records / decision_records FK to."""
    for task_id in _TASKS:
        await backend.tasks.save(
            Task(
                id=as_uuid(task_id),
                title=NotBlankStr(task_id),
                description=NotBlankStr("perf-index fixture"),
                type=TaskType.DEVELOPMENT,
                project=NotBlankStr("proj-perf"),
                created_by=NotBlankStr("system"),
            ),
        )


async def _seed_cost_records(backend: SQLitePersistenceBackend, n: int) -> None:
    for i in range(n):
        await backend.cost_records.append(
            CostRecord(
                agent_id=NotBlankStr(_AGENTS[i % len(_AGENTS)]),
                task_id=NotBlankStr(sid(_TASKS[i % len(_TASKS)])),
                provider=NotBlankStr("test-provider"),
                model=NotBlankStr("test-small-001"),
                input_tokens=10,
                output_tokens=20,
                cost=0.001,
                currency=CurrencyCode("USD"),
                timestamp=_BASE + timedelta(seconds=i),
            ),
        )


# aiosqlite has no equivalent of psycopg's ``sql.SQL`` /
# ``sql.Identifier`` safe-composition primitives, so we hand back full
# pre-built statements through hardcoded allowlists keyed on the test
# inputs. The keys are the same strings the test bodies pass so call
# sites stay readable; the planner sees only the values, which never
# embed call-site data.
_ALLOWED_ANALYZE_STMTS: dict[str, str] = {
    "cost_records": "ANALYZE cost_records",
    "decision_records": "ANALYZE decision_records",
}

_ALLOWED_EXPLAIN_STMTS: dict[str, str] = {
    "SELECT * FROM cost_records WHERE agent_id = ? ORDER BY timestamp DESC LIMIT 50": (
        "EXPLAIN QUERY PLAN SELECT * FROM cost_records "
        "WHERE agent_id = ? ORDER BY timestamp DESC LIMIT 50"
    ),
    "SELECT * FROM cost_records WHERE task_id = ? ORDER BY timestamp DESC LIMIT 50": (
        "EXPLAIN QUERY PLAN SELECT * FROM cost_records "
        "WHERE task_id = ? ORDER BY timestamp DESC LIMIT 50"
    ),
    "SELECT * FROM decision_records WHERE task_id = ? "
    "ORDER BY recorded_at ASC, id ASC LIMIT 50": (
        "EXPLAIN QUERY PLAN SELECT * FROM decision_records "
        "WHERE task_id = ? ORDER BY recorded_at ASC, id ASC LIMIT 50"
    ),
}


async def _explain_plan(
    backend: SQLitePersistenceBackend,
    sql: str,
    *params: object,
    analyze: str | None = None,
) -> str:
    db = backend._db
    assert db is not None, "fixture must connect the backend before EXPLAIN"
    if analyze is not None:
        analyze_stmt = _ALLOWED_ANALYZE_STMTS.get(analyze)
        assert analyze_stmt is not None, f"Unexpected ANALYZE target: {analyze}"
        await db.execute(analyze_stmt)
    explain_stmt = _ALLOWED_EXPLAIN_STMTS.get(sql)
    assert explain_stmt is not None, f"Unexpected EXPLAIN query shape: {sql}"
    cursor = await db.execute(explain_stmt, params)
    rows = await cursor.fetchall()
    return "\n".join(str(tuple(row)) for row in rows)


class TestCostRecordsCompositeIndexes:
    async def test_agent_timestamp_index_used(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        await _seed_tasks(on_disk_backend)
        await _seed_cost_records(on_disk_backend, n=200)
        plan = await _explain_plan(
            on_disk_backend,
            "SELECT * FROM cost_records WHERE agent_id = ? "
            "ORDER BY timestamp DESC LIMIT 50",
            _AGENTS[0],
            analyze="cost_records",
        )
        assert "idx_cost_records_agent_timestamp" in plan, (
            f"Composite (agent_id, timestamp DESC) index not used:\n{plan}"
        )

    async def test_task_timestamp_index_used(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        await _seed_tasks(on_disk_backend)
        await _seed_cost_records(on_disk_backend, n=200)
        plan = await _explain_plan(
            on_disk_backend,
            "SELECT * FROM cost_records WHERE task_id = ? "
            "ORDER BY timestamp DESC LIMIT 50",
            sid(_TASKS[0]),
            analyze="cost_records",
        )
        assert "idx_cost_records_task_timestamp" in plan, (
            f"Composite (task_id, timestamp DESC) index not used:\n{plan}"
        )


class TestDecisionRecordsCompositeIndex:
    async def test_task_recorded_id_index_used(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        await _seed_tasks(on_disk_backend)
        # Seed many rows for a SINGLE task so ``ORDER BY recorded_at,
        # id`` actually distinguishes rows and the planner must reach
        # for the composite index rather than satisfying the order
        # incidentally with one row per task.
        target_task = _TASKS[0]
        for i in range(200):
            await on_disk_backend.decision_records.append_with_next_version(
                record_id=NotBlankStr(f"dec-{i:03d}"),
                task_id=NotBlankStr(sid(target_task)),
                approval_id=None,
                executing_agent_id=NotBlankStr(_AGENTS[0]),
                reviewer_agent_id=NotBlankStr(_AGENTS[1]),
                decision=DecisionOutcome.APPROVED,
                reason=None,
                criteria_snapshot=(),
                recorded_at=_BASE + timedelta(seconds=i),
            )
        plan = await _explain_plan(
            on_disk_backend,
            "SELECT * FROM decision_records WHERE task_id = ? "
            "ORDER BY recorded_at ASC, id ASC LIMIT 50",
            sid(target_task),
            analyze="decision_records",
        )
        assert "idx_dr_task_recorded_id" in plan, (
            f"Composite (task_id, recorded_at, id) index not used:\n{plan}"
        )
