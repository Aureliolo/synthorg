"""End-to-end integration tests for PostgresPersistenceBackend.

Exercises the backend against a real Postgres 18 container via
testcontainers.  Complements the parametrized conformance suite in
``tests/conformance/persistence/`` by covering concurrency, pool
behavior, migration idempotency, and Postgres-native JSONB /
TIMESTAMPTZ wire-format round-trips.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from pydantic import SecretStr

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.communication.message import MessageMetadata
from synthorg.core.types import NotBlankStr
from synthorg.persistence.config import PostgresConfig
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from tests.unit.persistence.conftest import make_message, make_task


@pytest.mark.integration
class TestLifecycleIntegration:
    async def test_full_lifecycle_round_trip(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """Full connect + migrate + save + read + reconnect cycle.

        Persistence across a reconnect verifies that the on-disk
        state is not tied to the pool lifetime.
        """
        task = make_task(task_id="lifecycle-t1", project="lifecycle-proj")
        await postgres_backend.tasks.save(task)
        await postgres_backend.disconnect()

        await postgres_backend.connect()
        fetched = await postgres_backend.tasks.get("lifecycle-t1")
        assert fetched is not None
        assert fetched.id == "lifecycle-t1"
        assert fetched.project == "lifecycle-proj"

    async def test_health_check_round_trip(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        assert await postgres_backend.health_check() is True

    async def test_migration_idempotency(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """Running migrate twice is a no-op."""
        await postgres_backend.migrate()
        await postgres_backend.migrate()
        # Still connected, no error.
        assert postgres_backend.is_connected is True


@pytest.mark.integration
class TestConcurrentWrites:
    async def test_fifty_parallel_saves_fan_out(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """50 concurrent Task saves must all land without data loss."""
        task_ids = [f"concurrent-{i:03d}" for i in range(50)]

        async with asyncio.TaskGroup() as tg:
            for task_id in task_ids:
                task = make_task(task_id=task_id)
                tg.create_task(postgres_backend.tasks.save(task))

        # Verify every row is present.
        from synthorg.persistence.task_protocol import TaskFilterSpec

        all_tasks = await postgres_backend.tasks.query(TaskFilterSpec())
        saved_ids = {t.id for t in all_tasks}
        for tid in task_ids:
            assert tid in saved_ids

    async def test_concurrent_cost_record_saves(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """Cost records append-only against a shared parent task."""
        task = make_task(task_id="cost-parent")
        await postgres_backend.tasks.save(task)

        records = [
            CostRecord(
                agent_id=f"agent-{i}",
                task_id="cost-parent",
                provider="test-provider",
                model="test-small-001",
                input_tokens=10,
                output_tokens=10,
                cost=0.01 * (i + 1),
                currency="EUR",
                timestamp=datetime(2026, 4, 10, 12, i % 60, tzinfo=UTC),
                call_category=LLMCallCategory.PRODUCTIVE,
            )
            for i in range(20)
        ]

        async with asyncio.TaskGroup() as tg:
            for record in records:
                tg.create_task(postgres_backend.cost_records.append(record))

        total = await postgres_backend.cost_records.aggregate(task_id="cost-parent")
        # Sum of 0.01 * (1..20) = 0.21 * 10 = 2.10
        expected = sum(0.01 * (i + 1) for i in range(20))
        assert abs(total - expected) < 1e-9


@pytest.mark.integration
class TestPoolExhaustion:
    async def test_small_pool_under_load(
        self,
        postgres_container: object,
    ) -> None:
        """Pool with pool_max_size=2 handles queued requests cleanly."""
        from testcontainers.postgres import PostgresContainer

        container: PostgresContainer = postgres_container
        db_name = f"pool_test_{uuid4().hex}"

        admin_conninfo = psycopg.conninfo.make_conninfo(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
            user=container.username,
            password=container.password,
            dbname=container.dbname,
        )
        async with await psycopg.AsyncConnection.connect(
            admin_conninfo, autocommit=True
        ) as admin:
            await admin.execute(
                psycopg.sql.SQL("CREATE DATABASE {}").format(
                    psycopg.sql.Identifier(db_name)
                )
            )

        try:
            config = PostgresConfig(
                host=container.get_container_host_ip(),
                port=int(container.get_exposed_port(5432)),
                database=db_name,
                username=container.username,
                password=SecretStr(container.password),
                ssl_mode="disable",
                pool_min_size=1,
                pool_max_size=2,
                pool_timeout_seconds=30.0,
                connect_timeout_seconds=5.0,
            )
            backend = PostgresPersistenceBackend(config)
            await backend.connect()
            try:
                await backend.migrate()

                results: list[bool] = []

                async def run_query() -> None:
                    results.append(await backend.health_check())

                async with asyncio.TaskGroup() as tg:
                    for _ in range(10):
                        tg.create_task(run_query())

                assert all(results), f"some health checks failed: {results}"
            finally:
                await backend.disconnect()
        finally:
            async with await psycopg.AsyncConnection.connect(
                admin_conninfo, autocommit=True
            ) as admin:
                await admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid != pg_backend_pid()",
                    (db_name,),
                )
                await admin.execute(
                    psycopg.sql.SQL("DROP DATABASE IF EXISTS {}").format(
                        psycopg.sql.Identifier(db_name)
                    )
                )


@pytest.mark.integration
class TestNativePostgresTypes:
    async def test_jsonb_storage_is_native(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """Task.reviewers must be stored as real JSONB, not a TEXT blob."""
        task = make_task(task_id="jsonb-test")
        await postgres_backend.tasks.save(task)

        pool = postgres_backend.get_db()
        async with (
            pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "SELECT pg_typeof(reviewers)::text AS t FROM tasks WHERE id = %s",
                ("jsonb-test",),
            )
            row = await cur.fetchone()
            assert row is not None
            assert row["t"] == "jsonb"

    async def test_timestamptz_preserves_timezone(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """Round-trip of a cost record timestamp preserves microseconds."""
        task = make_task(task_id="tz-parent")
        await postgres_backend.tasks.save(task)

        precise_ts = datetime(2026, 4, 10, 12, 34, 56, 789012, tzinfo=UTC)
        record = CostRecord(
            agent_id="tz-agent",
            task_id="tz-parent",
            provider="test-provider",
            model="test-small-001",
            input_tokens=1,
            output_tokens=1,
            cost=0.001,
            currency="EUR",
            timestamp=precise_ts,
            call_category=LLMCallCategory.PRODUCTIVE,
        )
        await postgres_backend.cost_records.append(record)

        from synthorg.persistence.cost_record_protocol import (
            CostRecordFilterSpec,
        )

        results = await postgres_backend.cost_records.query(
            CostRecordFilterSpec(agent_id="tz-agent"),
        )
        assert len(results) == 1
        assert results[0].timestamp == precise_ts
        assert results[0].timestamp.tzinfo is not None

    async def test_message_metadata_jsonb_round_trip(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        """Message.metadata round-trips through JSONB preserving structure."""
        metadata = MessageMetadata(
            task_id=NotBlankStr("round-trip-task"),
            project_id=NotBlankStr("round-trip-project"),
            tokens_used=1234,
            cost=0.0425,
        )
        msg = make_message(
            msg_id=uuid4(),
            channel="jsonb-chan",
            content="payload",
            metadata=metadata,
        )
        await postgres_backend.messages.append(msg)

        history = await postgres_backend.messages.get_history("jsonb-chan")
        assert len(history) == 1
        # Assert the actual payload: a row-count-only assertion would
        # silently pass even if every metadata field were dropped on
        # the write or deserialized as ``None`` on the read path.
        fetched = history[0]
        assert fetched.metadata == metadata
        assert fetched.metadata.task_id == "round-trip-task"
        assert fetched.metadata.project_id == "round-trip-project"
        assert fetched.metadata.tokens_used == 1234
        assert fetched.metadata.cost == 0.0425


@pytest.mark.integration
class TestSettingsDelegation:
    async def test_get_setting_delegates_to_settings_repo(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        await postgres_backend.set_setting(NotBlankStr("test_key"), "test_value")
        value = await postgres_backend.get_setting(NotBlankStr("test_key"))
        assert value == "test_value"

    async def test_get_setting_missing_returns_none(
        self,
        postgres_backend: PostgresPersistenceBackend,
    ) -> None:
        assert await postgres_backend.get_setting(NotBlankStr("nonexistent")) is None
