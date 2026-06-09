# module-kind: tests
"""Critical-error carve-out for the atomic-swap rollback paths.

An ordinary swap failure rolls the original data back and re-raises.
``MemoryError`` / ``RecursionError`` must skip the rollback entirely:
the rollback itself does filesystem work that may allocate, and running
it under catastrophic interpreter state risks making things worse.
"""

from pathlib import Path

import pytest

from synthorg.backup.handlers.memory import MemoryComponentHandler
from synthorg.backup.handlers.sqlite_persistence import (
    SQLitePersistenceComponentHandler,
)

pytestmark = pytest.mark.unit


class TestSqliteSwapCarveout:
    """``_atomic_swap`` rollback runs for Exception, not MemoryError."""

    @pytest.fixture
    def paths(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        db_path = tmp_path / "live.db"
        db_path.write_bytes(b"original")
        bak_path = tmp_path / "live.db.bak"
        source = tmp_path / "restored.db"
        source.write_bytes(b"candidate")
        return db_path, source, bak_path

    def test_ordinary_failure_rolls_back(
        self,
        paths: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        db_path, source, bak_path = paths

        def _boom(*_a: object, **_k: object) -> None:
            msg = "copy failed"
            raise OSError(msg)

        monkeypatch.setattr(
            "synthorg.backup.handlers.sqlite_persistence.shutil.copy2", _boom
        )
        with pytest.raises(OSError, match="copy failed"):
            SQLitePersistenceComponentHandler._atomic_swap(db_path, source, bak_path)

        assert db_path.read_bytes() == b"original"
        assert not bak_path.exists()

    def test_memory_error_skips_rollback(
        self,
        paths: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        db_path, source, bak_path = paths

        def _boom(*_a: object, **_k: object) -> None:
            raise MemoryError

        monkeypatch.setattr(
            "synthorg.backup.handlers.sqlite_persistence.shutil.copy2", _boom
        )
        with pytest.raises(MemoryError):
            SQLitePersistenceComponentHandler._atomic_swap(db_path, source, bak_path)

        # Rollback must NOT have run: the original stays parked at .bak.
        assert bak_path.exists()
        assert not db_path.exists()


class TestMemorySwapCarveout:
    """Memory handler's ``_atomic_swap`` mirrors the sqlite carve-out."""

    @pytest.fixture
    def paths(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        data_path = tmp_path / "memory_data"
        data_path.mkdir()
        (data_path / "x.json").write_text("{}", encoding="utf-8")
        bak_path = tmp_path / "memory_data.bak"
        source = tmp_path / "restored_data"
        source.mkdir()
        return data_path, source, bak_path

    def test_ordinary_failure_rolls_back(
        self,
        paths: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data_path, source, bak_path = paths

        def _boom(*_a: object, **_k: object) -> None:
            msg = "copytree failed"
            raise OSError(msg)

        monkeypatch.setattr("synthorg.backup.handlers.memory.shutil.copytree", _boom)
        with pytest.raises(OSError, match="copytree failed"):
            MemoryComponentHandler._atomic_swap(data_path, source, bak_path)

        assert (data_path / "x.json").exists()
        assert not bak_path.exists()

    def test_memory_error_skips_rollback(
        self,
        paths: tuple[Path, Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data_path, source, bak_path = paths

        def _boom(*_a: object, **_k: object) -> None:
            raise MemoryError

        monkeypatch.setattr("synthorg.backup.handlers.memory.shutil.copytree", _boom)
        with pytest.raises(MemoryError):
            MemoryComponentHandler._atomic_swap(data_path, source, bak_path)

        # Rollback must NOT have run: the original stays parked at .bak.
        assert bak_path.exists()
        assert not data_path.exists()
