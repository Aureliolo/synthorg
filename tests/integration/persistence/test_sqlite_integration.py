"""Integration tests for SQLite persistence (on-disk)."""

from pathlib import Path

import aiosqlite
import pytest

from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from tests.unit.persistence.conftest import make_message, make_task

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]


class TestSQLiteOnDisk:
    async def test_wal_mode_enabled(self, db_path: str) -> None:
        """WAL journal mode is enabled for the on-disk SQLite database."""
        backend = SQLitePersistenceBackend(SQLiteConfig(path=db_path))
        await backend.connect()
        await backend.migrate()

        # Write some data to force WAL file creation
        task = make_task()
        await backend.tasks.save(task)

        assert Path(db_path).exists()  # noqa: ASYNC240
        await backend.disconnect()

        # Verify WAL mode by querying the journal_mode pragma
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA journal_mode")
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "wal"

    async def test_data_persists_across_reconnect(self, db_path: str) -> None:
        """Data written before disconnect is readable after reconnect."""
        backend = SQLitePersistenceBackend(SQLiteConfig(path=db_path))
        await backend.connect()
        await backend.migrate()

        task = make_task(task_id="persist-test")
        await backend.tasks.save(task)
        await backend.disconnect()

        # Reconnect and verify data
        backend2 = SQLitePersistenceBackend(SQLiteConfig(path=db_path))
        await backend2.connect()
        await backend2.migrate()

        result = await backend2.tasks.get("persist-test")
        assert result is not None
        assert result.id == "persist-test"
        await backend2.disconnect()

    async def test_multiple_entity_types_persist(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        """Tasks, cost records, and messages all persist together."""
        from datetime import UTC, datetime

        from synthorg.budget.cost_record import CostRecord

        backend = on_disk_backend

        # Save task
        await backend.tasks.save(make_task(task_id="multi-t1"))

        # Save cost record
        record = CostRecord(
            agent_id="alice",
            task_id="multi-t1",
            provider="test-provider",
            model="test-model-001",
            input_tokens=500,
            output_tokens=200,
            cost_usd=0.03,
            timestamp=datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        )
        await backend.cost_records.save(record)

        # Save message
        await backend.messages.save(make_message(channel="test-channel"))

        # Verify all persist
        tasks = await backend.tasks.list_tasks()
        assert len(tasks) == 1

        records = await backend.cost_records.query()
        assert len(records) == 1

        history = await backend.messages.get_history("test-channel")
        assert len(history) == 1

    async def test_concurrent_reads(self, db_path: str) -> None:
        """Multiple connections can read concurrently with WAL mode."""
        import asyncio

        # Set up data
        backend = SQLitePersistenceBackend(SQLiteConfig(path=db_path))
        await backend.connect()
        await backend.migrate()
        for i in range(10):
            await backend.tasks.save(make_task(task_id=f"conc-{i}"))
        await backend.disconnect()

        async def read_all() -> int:
            b = SQLitePersistenceBackend(SQLiteConfig(path=db_path))
            await b.connect()
            tasks = await b.tasks.list_tasks()
            await b.disconnect()
            return len(tasks)

        # Run multiple readers concurrently
        results = await asyncio.gather(read_all(), read_all(), read_all())
        assert all(r == 10 for r in results)
