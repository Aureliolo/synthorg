"""Fixtures for persistence integration tests."""

from typing import TYPE_CHECKING

import pytest

from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Return a temporary on-disk database path."""
    return str(tmp_path / "test.db")


@pytest.fixture
async def on_disk_backend(db_path: str) -> AsyncGenerator[SQLitePersistenceBackend]:
    """Connected + migrated on-disk SQLite backend."""
    backend = SQLitePersistenceBackend(SQLiteConfig(path=db_path))
    await backend.connect()
    await backend.migrate()
    yield backend
    await backend.disconnect()
