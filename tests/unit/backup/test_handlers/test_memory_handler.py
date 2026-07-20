"""Tests for MemoryComponentHandler."""

from pathlib import Path
from unittest.mock import patch

import pytest

from synthorg.backup.errors import ComponentBackupError
from synthorg.backup.handlers.memory import MemoryComponentHandler
from synthorg.backup.models import BackupComponent


def _populate_memory_dir(path: Path) -> int:
    """Create a fake memory directory tree and return total bytes.

    Mirrors the on-disk artefacts backends keep outside the operational
    database: materialised skill files and embedding checkpoints.
    """
    path.mkdir(parents=True, exist_ok=True)
    sub = path / "checkpoints"
    sub.mkdir()
    f1 = path / "SKILL.md"
    f1.write_bytes(b"x" * 100)
    f2 = sub / "embedder.bin"
    f2.write_bytes(b"y" * 200)
    return 300


# -- component property -------------------------------------------------------


@pytest.mark.unit
class TestComponentProperty:
    """MemoryComponentHandler.component returns MEMORY."""

    def test_returns_memory(self) -> None:
        handler = MemoryComponentHandler(Path("/some/path"))
        assert handler.component is BackupComponent.MEMORY


# -- backup --------------------------------------------------------------------


@pytest.mark.unit
class TestBackup:
    """MemoryComponentHandler.backup copies directory tree."""

    async def test_copies_directory_tree(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "memory_data"
        expected_size = _populate_memory_dir(data_dir)

        target_dir = tmp_path / "backup"
        target_dir.mkdir()

        handler = MemoryComponentHandler(data_dir)
        size = await handler.backup(target_dir)

        assert size == expected_size
        # Verify the copied tree structure
        assert (target_dir / "memory" / "SKILL.md").exists()
        assert (target_dir / "memory" / "checkpoints" / "embedder.bin").exists()

    async def test_returns_zero_if_source_missing(self, tmp_path: Path) -> None:
        handler = MemoryComponentHandler(tmp_path / "nonexistent")
        target_dir = tmp_path / "backup"
        target_dir.mkdir()

        size = await handler.backup(target_dir)
        assert size == 0

    async def test_raises_on_copy_failure(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "memory_data"
        _populate_memory_dir(data_dir)

        target_dir = tmp_path / "backup"
        target_dir.mkdir()

        handler = MemoryComponentHandler(data_dir)

        with (
            patch(
                "synthorg.backup.handlers.memory.MemoryComponentHandler._copy_tree",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(ComponentBackupError, match="Failed to back up memory"),
        ):
            await handler.backup(target_dir)


# -- restore -------------------------------------------------------------------


@pytest.mark.unit
class TestRestore:
    """MemoryComponentHandler.restore performs atomic swap."""

    async def test_restores_directory(self, tmp_path: Path) -> None:
        # Live data dir with original content
        live_dir = tmp_path / "live_memory"
        live_dir.mkdir()
        (live_dir / "old_file.txt").write_text("old")

        # Backup source with "memory" subdirectory
        backup_dir = tmp_path / "backup"
        memory_backup = backup_dir / "memory"
        _populate_memory_dir(memory_backup)

        handler = MemoryComponentHandler(live_dir)
        await handler.restore(backup_dir)

        # Live dir should now contain restored data
        assert (live_dir / "SKILL.md").exists()
        assert (live_dir / "checkpoints" / "embedder.bin").exists()
        # Old file should be gone (replaced by backup copy)
        assert not (live_dir / "old_file.txt").exists()

    async def test_raises_if_backup_dir_missing(self, tmp_path: Path) -> None:
        handler = MemoryComponentHandler(tmp_path / "live")
        empty_dir = tmp_path / "empty_backup"
        empty_dir.mkdir()

        with pytest.raises(
            ComponentBackupError,
            match="Backup memory directory not found",
        ):
            await handler.restore(empty_dir)

    async def test_rolls_back_on_failure(self, tmp_path: Path) -> None:
        # Live data dir with content we want preserved on failure
        live_dir = tmp_path / "live_memory"
        live_dir.mkdir()
        (live_dir / "important.txt").write_text("keep me")

        # Backup source
        backup_dir = tmp_path / "backup"
        memory_backup = backup_dir / "memory"
        memory_backup.mkdir(parents=True)
        (memory_backup / "data.bin").write_bytes(b"backup")

        handler = MemoryComponentHandler(live_dir)

        with (
            patch(
                "synthorg.backup.handlers.memory.shutil.copytree",
                side_effect=OSError("permission denied"),
            ),
            pytest.raises(ComponentBackupError),
        ):
            await handler.restore(backup_dir)

        # Original data should be rolled back
        assert live_dir.exists()
        assert (live_dir / "important.txt").read_text() == "keep me"

    async def test_restore_works_without_existing_live_dir(
        self, tmp_path: Path
    ) -> None:
        live_dir = tmp_path / "new_memory"
        # No live dir exists yet

        backup_dir = tmp_path / "backup"
        memory_backup = backup_dir / "memory"
        _populate_memory_dir(memory_backup)

        handler = MemoryComponentHandler(live_dir)
        await handler.restore(backup_dir)

        assert live_dir.exists()
        assert (live_dir / "SKILL.md").exists()


# -- validate_source -----------------------------------------------------------


@pytest.mark.unit
class TestValidateSource:
    """MemoryComponentHandler.validate_source checks for directory."""

    async def test_returns_true_when_memory_subdir_exists(self, tmp_path: Path) -> None:
        (tmp_path / "memory").mkdir()
        handler = MemoryComponentHandler(Path("/unused"))
        assert await handler.validate_source(tmp_path) is True

    async def test_returns_false_when_memory_subdir_missing(
        self, tmp_path: Path
    ) -> None:
        handler = MemoryComponentHandler(Path("/unused"))
        assert await handler.validate_source(tmp_path) is False

    async def test_returns_false_for_nonexistent_dir(self, tmp_path: Path) -> None:
        handler = MemoryComponentHandler(Path("/unused"))
        assert await handler.validate_source(tmp_path / "nope") is False
