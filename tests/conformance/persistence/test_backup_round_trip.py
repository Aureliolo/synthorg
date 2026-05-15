"""Dual-backend conformance for persistence backup handlers.

Exercises backup creation and structural validation through whichever
persistence backend the conformance ``backend`` fixture supplied. The
SQLite arm goes through ``VACUUM INTO`` + ``PRAGMA integrity_check``;
the Postgres arm goes through ``pg_dump`` for backup and
``pg_restore --list`` for validation. The Postgres arm is skipped when
``pg_dump`` or ``pg_restore`` is not on PATH (e.g. a dev workstation
without the postgres-client package); CI provisions the binaries
alongside the testcontainers postgres image.
"""

import shutil
from pathlib import Path
from typing import assert_never, cast

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


def _build_handler(
    backend: PersistenceBackend,
) -> SQLitePersistenceComponentHandler | PostgresPersistenceComponentHandler:
    """Pick a backup handler for ``backend`` via its ``kind`` discriminator.

    Uses the public ``backend.kind`` plus ``backend.config`` accessors
    so the conformance test does not depend on either the backend's
    concrete class or its private ``_config`` attribute.
    """
    if backend.kind == "sqlite":
        sqlite_backend = cast(SQLitePersistenceBackend, backend)
        return SQLitePersistenceComponentHandler(
            db_path=Path(sqlite_backend.config.path),
        )
    if backend.kind == "postgres":
        if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
            pytest.skip("pg_dump / pg_restore binaries are not available on PATH")
        postgres_backend = cast(PostgresPersistenceBackend, backend)
        return PostgresPersistenceComponentHandler(config=postgres_backend.config)
    assert_never(backend.kind)


async def test_backup_handler_round_trip(
    backend: PersistenceBackend,
    tmp_path: Path,
) -> None:
    """``backup -> validate_source`` succeeds for the active backend."""
    target_dir = tmp_path / "backup"
    target_dir.mkdir()

    handler = _build_handler(backend)
    size = await handler.backup(target_dir)
    assert size > 0
    assert await handler.validate_source(target_dir) is True
