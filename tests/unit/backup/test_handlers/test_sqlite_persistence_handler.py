"""Tests for SQLitePersistenceComponentHandler."""

import sqlite3
from pathlib import Path

import pytest

from synthorg.backup.errors import ComponentBackupError
from synthorg.backup.handlers.sqlite_persistence import (
    SQLitePersistenceComponentHandler,
)
from synthorg.backup.models import BackupComponent
from synthorg.persistence.sqlite.backup_utils import IntegrityCheckError


def _create_test_db(path: Path) -> None:
    """Create a minimal valid SQLite database at *path*."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO t (val) VALUES ('hello')")
        conn.commit()
    finally:
        conn.close()


def _corrupt_file(path: Path) -> None:
    """Write garbage bytes to make the file look like a corrupt DB."""
    path.write_bytes(b"NOT-A-SQLITE-DATABASE" * 50)


# -- component property -------------------------------------------------------


@pytest.mark.unit
class TestComponentProperty:
    """SQLitePersistenceComponentHandler.component returns PERSISTENCE."""

    def test_returns_persistence(self, tmp_path: Path) -> None:
        handler = SQLitePersistenceComponentHandler(tmp_path / "db.sqlite")
        assert handler.component is BackupComponent.PERSISTENCE


# -- backup --------------------------------------------------------------------


@pytest.mark.unit
class TestBackup:
    """SQLitePersistenceComponentHandler.backup creates a valid SQLite copy."""

    async def test_backup_creates_valid_copy(self, tmp_path: Path) -> None:
        db_path = tmp_path / "source" / "synthorg.db"
        db_path.parent.mkdir()
        _create_test_db(db_path)

        target_dir = tmp_path / "backup"
        target_dir.mkdir()

        handler = SQLitePersistenceComponentHandler(db_path)
        size = await handler.backup(target_dir)

        backup_file = target_dir / "synthorg.db"
        assert backup_file.exists()
        assert size > 0
        assert size == backup_file.stat().st_size

        # Verify the copy is a valid SQLite DB with the same data
        conn = sqlite3.connect(str(backup_file))
        try:
            rows = conn.execute("SELECT val FROM t").fetchall()
            assert rows == [("hello",)]
        finally:
            conn.close()

    async def test_backup_raises_on_invalid_source(self, tmp_path: Path) -> None:
        handler = SQLitePersistenceComponentHandler(
            tmp_path / "nonexistent" / "nope.db",
        )
        target_dir = tmp_path / "backup"
        target_dir.mkdir()

        with pytest.raises(ComponentBackupError, match="Failed to back up"):
            await handler.backup(target_dir)

    async def test_backup_raises_on_corrupt_source(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corrupt.db"
        _corrupt_file(db_path)

        target_dir = tmp_path / "backup"
        target_dir.mkdir()

        handler = SQLitePersistenceComponentHandler(db_path)
        with pytest.raises(ComponentBackupError):
            await handler.backup(target_dir)


# -- restore -------------------------------------------------------------------


@pytest.mark.unit
class TestRestore:
    """SQLitePersistenceComponentHandler.restore performs atomic swap."""

    async def test_restore_replaces_live_db(self, tmp_path: Path) -> None:
        # Set up the "live" DB with original data
        live_db = tmp_path / "live" / "synthorg.db"
        live_db.parent.mkdir()
        _create_test_db(live_db)

        # Set up the "backup" DB with different data
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        backup_db = backup_dir / "synthorg.db"
        conn = sqlite3.connect(str(backup_db))
        try:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
            conn.execute("INSERT INTO t (val) VALUES ('restored')")
            conn.commit()
        finally:
            conn.close()

        handler = SQLitePersistenceComponentHandler(live_db)
        await handler.restore(backup_dir)

        # Live DB now has the restored data
        conn = sqlite3.connect(str(live_db))
        try:
            rows = conn.execute("SELECT val FROM t").fetchall()
            assert rows == [("restored",)]
        finally:
            conn.close()

        # .bak should be cleaned up on success
        bak_path = live_db.with_suffix(".db.bak")
        assert not bak_path.exists()

    async def test_restore_raises_if_backup_missing(self, tmp_path: Path) -> None:
        handler = SQLitePersistenceComponentHandler(tmp_path / "live.db")
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        with pytest.raises(ComponentBackupError, match="Backup database not found"):
            await handler.restore(empty_dir)

    async def test_restore_rolls_back_on_corrupt_backup(self, tmp_path: Path) -> None:
        # Set up a valid live DB
        live_db = tmp_path / "live" / "synthorg.db"
        live_db.parent.mkdir()
        _create_test_db(live_db)

        # Create a corrupt backup file
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        corrupt_backup = backup_dir / "synthorg.db"
        _corrupt_file(corrupt_backup)

        handler = SQLitePersistenceComponentHandler(live_db)

        with pytest.raises(ComponentBackupError):
            await handler.restore(backup_dir)

        # Original DB should be rolled back
        assert live_db.exists()
        conn = sqlite3.connect(str(live_db))
        try:
            rows = conn.execute("SELECT val FROM t").fetchall()
            assert rows == [("hello",)]
        finally:
            conn.close()

    async def test_restore_works_when_no_live_db(self, tmp_path: Path) -> None:
        live_db = tmp_path / "live" / "synthorg.db"
        live_db.parent.mkdir()
        # No live DB; restore should still succeed.

        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        backup_db = backup_dir / "synthorg.db"
        _create_test_db(backup_db)

        handler = SQLitePersistenceComponentHandler(live_db)
        await handler.restore(backup_dir)

        assert live_db.exists()
        conn = sqlite3.connect(str(live_db))
        try:
            rows = conn.execute("SELECT val FROM t").fetchall()
            assert rows == [("hello",)]
        finally:
            conn.close()


# -- validate_source -----------------------------------------------------------


@pytest.mark.unit
class TestValidateSource:
    """SQLitePersistenceComponentHandler.validate_source checks integrity."""

    async def test_returns_true_for_valid_db(self, tmp_path: Path) -> None:
        db_file = tmp_path / "synthorg.db"
        _create_test_db(db_file)

        handler = SQLitePersistenceComponentHandler(tmp_path / "unused.db")
        assert await handler.validate_source(tmp_path) is True

    async def test_returns_false_for_missing_file(self, tmp_path: Path) -> None:
        handler = SQLitePersistenceComponentHandler(tmp_path / "unused.db")
        assert await handler.validate_source(tmp_path) is False

    async def test_corrupt_file_raises_component_backup_error(
        self, tmp_path: Path
    ) -> None:
        # A file that is not a SQLite database at all is a "could not
        # run integrity check" condition, distinct from "the check
        # itself reported corruption".  The handler surfaces this as
        # ComponentBackupError so callers never silently treat an
        # undeterminable backup as clean-failure.
        corrupt = tmp_path / "synthorg.db"
        _corrupt_file(corrupt)

        handler = SQLitePersistenceComponentHandler(tmp_path / "unused.db")
        with pytest.raises(ComponentBackupError):
            await handler.validate_source(tmp_path)


# -- _check_integrity ----------------------------------------------------------


@pytest.mark.unit
class TestCheckIntegrity:
    """Static _check_integrity method validates SQLite databases."""

    def test_valid_db_passes(self, tmp_path: Path) -> None:
        db_file = tmp_path / "good.db"
        _create_test_db(db_file)
        assert SQLitePersistenceComponentHandler._check_integrity(str(db_file)) is True

    def test_corrupt_db_raises(self, tmp_path: Path) -> None:
        db_file = tmp_path / "bad.db"
        _corrupt_file(db_file)
        # _check_integrity now surfaces sqlite3 failures as
        # IntegrityCheckError so callers can distinguish "could not
        # check" from "check reported corruption".
        with pytest.raises(IntegrityCheckError):
            SQLitePersistenceComponentHandler._check_integrity(str(db_file))

    def test_empty_file_is_treated_as_new_db(self, tmp_path: Path) -> None:
        db_file = tmp_path / "empty.db"
        db_file.write_bytes(b"")
        # SQLite treats a zero-byte file as a new, valid database;
        # PRAGMA integrity_check returns "ok".
        assert SQLitePersistenceComponentHandler._check_integrity(str(db_file)) is True
