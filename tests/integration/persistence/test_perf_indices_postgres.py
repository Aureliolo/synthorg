"""Integration tests: Postgres composite indexes for cost_records / decision_records.

The EXPLAIN-asserting cases use a simpler ``ORDER BY`` than the
production repo query (which trails ``agent_id ASC, rowid ASC`` as
deterministic tiebreakers). The composite index is the right shape
for cursor-pagination queries (single-column trailing ORDER BY) and
that is what these tests pin. Production queries with the longer
ORDER BY remain served by the existing single-column indexes; the new
composites earn their keep on the cursor-paginated query shapes we
are about to introduce.
"""

from datetime import UTC, datetime, timedelta

import pytest
from psycopg import sql

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import CurrencyCode
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decisions import DecisionOutcome
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration


_AGENTS = tuple(f"agent-{i:02d}" for i in range(5))
_TASKS = tuple(f"task-{i:03d}" for i in range(20))
_BASE = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


async def _seed_tasks(backend: PostgresPersistenceBackend) -> None:
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


async def _seed_cost_records(backend: PostgresPersistenceBackend, n: int) -> None:
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


async def _explain_plan(
    backend: PostgresPersistenceBackend,
    query: sql.SQL,
    *params: object,
    table: str,
    drop_indexes: tuple[str, ...] = (),
) -> str:
    pool = backend._pool
    assert pool is not None, "fixture must connect the backend before EXPLAIN"
    analyze_stmt = sql.SQL("ANALYZE {}").format(sql.Identifier(table))
    explain_stmt = sql.SQL("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {}").format(query)
    # ``force_rollback`` runs everything (including any DROP INDEX) inside a
    # transaction that is always rolled back, so dropping a competitor index
    # to pin the composite-index plan never mutates the session-scoped schema.
    async with pool.connection() as conn, conn.transaction(force_rollback=True):
        for index_name in drop_indexes:
            await conn.execute(
                sql.SQL("DROP INDEX IF EXISTS {}").format(sql.Identifier(index_name))
            )
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
            sid(_TASKS[0]),
            table="cost_records",
            # ``idx_cost_records_task_id`` (single column) lets the planner
            # satisfy the filter with a small bitmap scan and a tiny sort at
            # toy row counts; drop it so the planner must show the composite
            # index serves both the filter and the order.
            drop_indexes=("idx_cost_records_task_id",),
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
        # Seed many rows for a SINGLE task so ``ORDER BY recorded_at,
        # id`` is genuinely discriminative and the planner has to lean
        # on the composite index rather than satisfying the order
        # incidentally with one row per task.
        target_task = _TASKS[0]
        for i in range(200):
            await postgres_backend.decision_records.append_with_next_version(
                record_id=sid(f"dec-{i:03d}"),
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
            postgres_backend,
            sql.SQL(
                "SELECT * FROM decision_records WHERE task_id = %s "
                "ORDER BY recorded_at ASC, id ASC LIMIT 50",
            ),
            sid(target_task),
            table="decision_records",
        )
        assert "idx_dr_task_recorded_id" in plan, (
            f"Composite (task_id, recorded_at, id) index not used:\n{plan}"
        )


async def _seed_conversation_family(backend: PostgresPersistenceBackend) -> None:
    pool = backend._pool
    assert pool is not None, "fixture must connect the backend before seeding"
    conv = sid("conv-000")
    async with pool.connection() as conn, conn.transaction():
        for i in range(10):
            await conn.execute(
                "INSERT INTO conversations "
                "(id, created_by, created_at, updated_at) VALUES (%s, %s, %s, %s)",
                (
                    sid(f"conv-{i:03d}"),
                    "system",
                    _BASE + timedelta(seconds=i),
                    _BASE + timedelta(seconds=i),
                ),
            )
        for i in range(10):
            await conn.execute(
                "INSERT INTO conversational_proposals "
                "(id, conversation_id, approval_id, work_item_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    sid(f"prop-{i:03d}"),
                    conv,
                    sid(f"appr-{i:03d}"),
                    "{}",
                    _BASE + timedelta(seconds=i),
                ),
            )
        for i in range(10):
            await conn.execute(
                "INSERT INTO conversation_invites "
                "(id, conversation_id, approval_id, requested_by_agent_id, "
                "target_agent_id, reason, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    sid(f"inv-{i:03d}"),
                    conv,
                    sid(f"iappr-{i:03d}"),
                    _AGENTS[0],
                    _AGENTS[i % len(_AGENTS)],
                    "perf-index fixture",
                    _BASE + timedelta(seconds=i),
                ),
            )


async def _seed_webhook_receipts(backend: PostgresPersistenceBackend) -> None:
    pool = backend._pool
    assert pool is not None, "fixture must connect the backend before seeding"
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO connections (name, connection_type, auth_method, "
            "created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
            ("conn-perf", "generic_http", "api_key", _BASE, _BASE),
        )
        for i in range(10):
            await conn.execute(
                "INSERT INTO webhook_receipts (id, connection_name, received_at) "
                "VALUES (%s, %s, %s)",
                (sid(f"wh-{i:03d}"), "conn-perf", _BASE + timedelta(seconds=i)),
            )


class TestConversationFamilyPerfIndices:
    async def test_conversations_list_index_used(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _seed_conversation_family(postgres_backend)
        plan = await _explain_plan(
            postgres_backend,
            sql.SQL(
                "SELECT * FROM conversations "
                "ORDER BY created_at DESC, id DESC LIMIT 50",
            ),
            table="conversations",
        )
        assert "idx_conversations_created_id" in plan, (
            f"(created_at DESC, id DESC) index not used for list_items:\n{plan}"
        )

    async def test_proposals_by_conversation_index_used(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _seed_conversation_family(postgres_backend)
        plan = await _explain_plan(
            postgres_backend,
            sql.SQL(
                "SELECT * FROM conversational_proposals WHERE conversation_id = %s "
                "ORDER BY created_at DESC, id DESC LIMIT 50",
            ),
            sid("conv-000"),
            table="conversational_proposals",
        )
        assert "idx_cp_conversation_id" in plan, (
            f"(conversation_id, created_at DESC) index not used:\n{plan}"
        )

    async def test_invites_by_target_agent_index_used(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _seed_conversation_family(postgres_backend)
        plan = await _explain_plan(
            postgres_backend,
            sql.SQL(
                "SELECT * FROM conversation_invites "
                "WHERE target_agent_id = %s LIMIT 50",
            ),
            _AGENTS[0],
            table="conversation_invites",
        )
        assert "idx_cinv_target_agent_id" in plan, (
            f"target_agent_id index not used for the status-agnostic query:\n{plan}"
        )

    async def test_webhook_receipts_list_index_used(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await _seed_webhook_receipts(postgres_backend)
        plan = await _explain_plan(
            postgres_backend,
            sql.SQL(
                "SELECT * FROM webhook_receipts "
                "ORDER BY received_at DESC, id DESC LIMIT 50",
            ),
            table="webhook_receipts",
        )
        assert "idx_webhook_receipts_received_id" in plan, (
            f"(received_at DESC, id DESC) index not used for list_items:\n{plan}"
        )
