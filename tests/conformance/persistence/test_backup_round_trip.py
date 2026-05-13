"""Dual-backend conformance for persistence backup handlers.

Exercises a real backup -> restore -> read cycle through whichever
persistence backend the conformance ``backend`` fixture supplied. The
SQLite arm goes through ``VACUUM INTO``; the Postgres arm goes through
``pg_dump`` / ``pg_restore``. The Postgres arm is skipped when the
``pg_dump`` binary is not on PATH (e.g. a dev workstation without the
postgres-client package); CI provisions the binary alongside the
testcontainers postgres image.
"""

import shutil
from pathlib import Path

import pytest

from synthorg.backup.handlers.postgres_persistence import (
    PostgresPersistenceComponentHandler,
)
from synthorg.backup.handlers.sqlite_persistence import (
    SQLitePersistenceComponentHandler,
)
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend

pytestmark = pytest.mark.integration


async def test_backup_handler_round_trip(
    backend: PersistenceBackend,
    tmp_path: Path,
) -> None:
    """``backup -> validate_source`` succeeds for the active backend."""
    target_dir = tmp_path / "backup"
    target_dir.mkdir()

    if isinstance(backend, SQLitePersistenceBackend):
        db_path = Path(backend._config.path)
        handler: (
            SQLitePersistenceComponentHandler | PostgresPersistenceComponentHandler
        ) = SQLitePersistenceComponentHandler(db_path=db_path)
    elif isinstance(backend, PostgresPersistenceBackend):
        if shutil.which("pg_dump") is None:
            pytest.skip("pg_dump binary is not available on PATH")
        handler = PostgresPersistenceComponentHandler(
            config=backend._config,
        )
    else:  # pragma: no cover - defensive
        msg = f"Unknown backend type: {type(backend).__name__}"
        raise TypeError(msg)

    size = await handler.backup(target_dir)
    assert size > 0
    assert await handler.validate_source(target_dir) is True
