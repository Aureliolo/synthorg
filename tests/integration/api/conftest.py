"""Fixtures shared by tests under ``tests/integration/api/``.

Provides an on-disk SQLite persistence backend fixture mirroring the
one in ``tests/integration/persistence/conftest.py`` so API-level
integration tests can exercise real persistence without duplicating
bootstrap boilerplate in every test module.
"""

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from synthorg.persistence import migrations
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend


async def _isolated_sqlite_migrate(db_path: str, tmp_path: Path) -> None:
    """Apply SQLite migrations against a per-test isolated revisions copy.

    Each xdist worker has its own SQLite file (per ``tmp_path``), so
    yoyo's DB-level lock cannot contend across workers; the per-test
    revisions copy keeps the on-disk layout symmetric with production.
    """
    revisions_path = migrations.copy_revisions(
        tmp_path / f"sqlite_revisions_{uuid.uuid4().hex}",
        backend="sqlite",
    )
    await migrations.migrate_apply(
        migrations.to_sqlite_url(db_path),
        revisions_path=revisions_path,
        backend="sqlite",
    )


@pytest.fixture
async def on_disk_backend(
    tmp_path: Path,
) -> AsyncGenerator[SQLitePersistenceBackend]:
    """Connected + migrated on-disk SQLite backend for API integration tests."""
    db_path = str(tmp_path / "test.db")
    backend = SQLitePersistenceBackend(SQLiteConfig(path=db_path))
    await backend.connect()
    try:
        await _isolated_sqlite_migrate(db_path, tmp_path)
        yield backend
    finally:
        await backend.disconnect()
