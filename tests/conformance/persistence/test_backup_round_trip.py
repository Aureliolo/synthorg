"""Dual-backend conformance for persistence backup handlers.

Exercises the whole backup -> validate -> restore cycle through whichever
persistence backend the conformance ``backend`` fixture supplied. The
SQLite arm goes through ``VACUUM INTO`` + ``PRAGMA integrity_check`` and a
file swap; the Postgres arm goes through ``pg_dump`` for backup,
``pg_restore --list`` for validation and ``pg_restore`` for the restore. The
Postgres arm is skipped when ``pg_dump`` or ``pg_restore`` is not on PATH
(e.g. a dev workstation without the postgres-client package); CI provisions
the binaries alongside the testcontainers postgres image.
"""

import os
import shutil
from pathlib import Path
from typing import assert_never

import pytest

from synthorg.backup.handlers.postgres_persistence import (
    PostgresPersistenceComponentHandler,
)
from synthorg.backup.handlers.sqlite_persistence import (
    SQLitePersistenceComponentHandler,
)
from synthorg.persistence.config import PostgresConfig, SQLiteConfig
from synthorg.persistence.protocol import PersistenceBackend, PersistenceBackendKind
from tests._shared import sid
from tests.unit.persistence.conftest import make_task

pytestmark = pytest.mark.integration


def _build_handler(
    backend: PersistenceBackend,
) -> SQLitePersistenceComponentHandler | PostgresPersistenceComponentHandler:
    """Pick a backup handler for ``backend`` via its ``kind`` discriminator.

    Uses the public ``backend.kind`` plus ``backend.config`` accessors
    so the conformance test does not depend on either the backend's
    concrete class or its private ``_config`` attribute. ``config`` is typed as
    the dialect-uniform union, so each arm narrows it the same way the
    production dispatch in ``backup/registry.py`` does.
    """
    config = backend.config
    if backend.kind == PersistenceBackendKind.SQLITE:
        assert isinstance(config, SQLiteConfig)
        return SQLitePersistenceComponentHandler(db_path=Path(config.path))
    if backend.kind == PersistenceBackendKind.POSTGRES:
        if os.name == "nt":
            # Not a gap to close: the two requirements are mutually exclusive
            # on Windows. psycopg's async path needs a SelectorEventLoop (which
            # this directory's conftest pins for exactly that reason) and
            # asyncio subprocesses need a ProactorEventLoop, and this arm does
            # both: it talks to the database through psycopg and shells out to
            # pg_dump. CI runs Linux, where one loop serves both.
            pytest.skip(
                "the postgres arm needs psycopg (SelectorEventLoop) and "
                "pg_dump (ProactorEventLoop) in one loop; CI runs it on Linux"
            )
        if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
            pytest.skip("pg_dump / pg_restore binaries are not available on PATH")
        assert isinstance(config, PostgresConfig)
        return PostgresPersistenceComponentHandler(config=config)
    assert_never(backend.kind)


async def test_backup_handler_round_trip(
    backend: PersistenceBackend,
    tmp_path: Path,
) -> None:
    """A row deleted after the backup comes back when the backup is restored.

    Asserting on data rather than on a zero exit status is the whole point: a
    restore that connects to nothing exits cleanly and changes nothing, so a
    ``restore`` call with no observable effect asserted passes either way.
    """
    target_dir = tmp_path / "backup"
    target_dir.mkdir()
    handler = _build_handler(backend)

    task = make_task(task_id="backup-round-trip", title="Survives a restore")
    await backend.tasks.save(task)

    size = await handler.backup(target_dir)
    assert size > 0
    assert await handler.validate_source(target_dir) is True

    await backend.tasks.delete(sid("backup-round-trip"))
    assert await backend.tasks.get(sid("backup-round-trip")) is None

    # Restore replaces the database under any open handle, which is the
    # handler's documented single-owner precondition rather than a test
    # convenience; reconnecting afterwards also rebuilds the repositories, so
    # the read below goes through the restored database and not a stale one.
    await backend.disconnect()
    await handler.restore(target_dir)
    await backend.connect()

    restored = await backend.tasks.get(sid("backup-round-trip"))
    assert restored is not None
    assert restored.title == "Survives a restore"
