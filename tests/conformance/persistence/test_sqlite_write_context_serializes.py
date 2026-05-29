"""SQLite ``write_context`` enforces strict mutual exclusion.

The Postgres arm of ``write_context`` is a no-op (pool checkouts
isolate writers at the database level), so a cross-backend test
cannot assert serialization. This file constructs a SQLite backend
directly and asserts that concurrent workers observe strict
interleave: every worker fully exits before the next enters.
"""
# lint-allow: dual-backend-parity -- write_context serialization is backend-specific by contract  # noqa: E501

import asyncio
from pathlib import Path

import pytest

from synthorg.persistence import migrations
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend

_WORKER_COUNT = 4


@pytest.mark.integration
async def test_sqlite_write_context_strictly_serializes_writers(
    tmp_path: Path,
) -> None:  # lint-allow: dual-backend-parity -- SQLite-only by design
    db_path = tmp_path / "serialization.db"
    rev_path = migrations.copy_revisions(tmp_path / "revisions", backend="sqlite")
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)),
        revisions_path=rev_path,
    )
    backend = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
    await backend.connect()
    try:
        events: list[tuple[str, int]] = []

        async def worker(idx: int) -> None:
            async with backend.write_context():
                events.append(("enter", idx))
                await asyncio.sleep(0)
                events.append(("exit", idx))

        async with asyncio.TaskGroup() as tg:
            for i in range(_WORKER_COUNT):
                _ = tg.create_task(worker(i))

        assert len(events) == 2 * _WORKER_COUNT
        for n in range(0, len(events), 2):
            assert events[n][0] == "enter"
            assert events[n + 1] == ("exit", events[n][1]), (
                f"Worker {events[n][1]} did not fully exit before the next "
                f"writer entered: events={events}"
            )
    finally:
        await backend.disconnect()
