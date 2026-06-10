# module-kind: tests
"""Critical-error carve-out for the archive mixin's broad except sites.

The manifest/archive probe helpers swallow ordinary corruption (a bad
backup must never break listing or matching), but ``MemoryError`` /
``RecursionError`` must escape so catastrophic interpreter state
reaches the supervisor instead of being absorbed as a
``BACKUP_MANIFEST_INVALID`` warning.
"""

import json
from pathlib import Path

import pytest

from synthorg.backup.config import BackupConfig, RetentionConfig
from synthorg.backup.models import BackupComponent
from synthorg.backup.service import BackupService

pytestmark = pytest.mark.unit


def _make_service(backup_path: Path) -> BackupService:
    config = BackupConfig(
        enabled=True,
        path=str(backup_path),
        schedule_hours=6,
        compression=False,
        include=(BackupComponent.CONFIG,),
        retention=RetentionConfig(max_count=10, max_age_days=30),
    )
    return BackupService(config, {})


def _write_dir_backup(backup_path: Path, payload: str) -> Path:
    backup_dir = backup_path / "backup-1_manual"
    backup_dir.mkdir(parents=True)
    (backup_dir / "manifest.json").write_text(payload, encoding="utf-8")
    return backup_dir


class TestDirManifestProbes:
    """Directory-manifest parse paths: swallow corruption, raise critical."""

    async def test_invalid_json_skips_entry(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        _write_dir_backup(tmp_path, "{not json")

        assert await service.list_backups() == ()

    async def test_memory_error_propagates_from_listing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = _make_service(tmp_path)
        _write_dir_backup(tmp_path, "{}")

        def _boom(*_a: object, **_k: object) -> object:
            raise MemoryError

        monkeypatch.setattr("synthorg.backup.service_archive.json.loads", _boom)
        with pytest.raises(MemoryError):
            await service.list_backups()

    async def test_recursion_error_propagates_from_listing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = _make_service(tmp_path)
        _write_dir_backup(tmp_path, "{}")

        def _boom(*_a: object, **_k: object) -> object:
            raise RecursionError

        monkeypatch.setattr("synthorg.backup.service_archive.json.loads", _boom)
        with pytest.raises(RecursionError):
            await service.list_backups()

    async def test_memory_error_propagates_from_dir_match(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = _make_service(tmp_path)
        backup_dir = _write_dir_backup(tmp_path, json.dumps({"backup_id": "x"}))

        def _boom(*_a: object, **_k: object) -> object:
            raise MemoryError

        monkeypatch.setattr("synthorg.backup.service_archive.json.loads", _boom)
        with pytest.raises(MemoryError):
            service._dir_matches_backup(backup_dir, "x")

    async def test_recursion_error_propagates_from_dir_match(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = _make_service(tmp_path)
        backup_dir = _write_dir_backup(tmp_path, json.dumps({"backup_id": "x"}))

        def _boom(*_a: object, **_k: object) -> object:
            raise RecursionError

        monkeypatch.setattr("synthorg.backup.service_archive.json.loads", _boom)
        with pytest.raises(RecursionError):
            service._dir_matches_backup(backup_dir, "x")

    def test_dir_match_ordinary_error_returns_false(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        backup_dir = _write_dir_backup(tmp_path, "{broken")

        assert service._dir_matches_backup(backup_dir, "x") is False


class TestArchiveManifestProbes:
    """Archive probe wrappers: swallow unexpected errors, raise critical."""

    @pytest.fixture
    def archive_entry(self, tmp_path: Path) -> Path:
        entry = tmp_path / "backup-2_manual.tar.gz"
        entry.write_bytes(b"not a real tarball")
        return entry

    async def test_unexpected_error_skips_archive_entry(
        self,
        tmp_path: Path,
        archive_entry: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = _make_service(tmp_path)

        def _boom(_path: Path) -> object:
            msg = "unexpected probe failure"
            raise RuntimeError(msg)

        monkeypatch.setattr(
            BackupService,
            "_read_manifest_from_archive",
            staticmethod(_boom),
        )
        assert await service.list_backups() == ()

    async def test_memory_error_propagates_from_archive_listing(
        self,
        tmp_path: Path,
        archive_entry: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = _make_service(tmp_path)

        def _boom(_path: Path) -> object:
            raise MemoryError

        monkeypatch.setattr(
            BackupService,
            "_read_manifest_from_archive",
            staticmethod(_boom),
        )
        with pytest.raises(MemoryError):
            await service.list_backups()

    async def test_recursion_error_propagates_from_archive_listing(
        self,
        tmp_path: Path,
        archive_entry: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = _make_service(tmp_path)

        def _boom(_path: Path) -> object:
            raise RecursionError

        monkeypatch.setattr(
            BackupService,
            "_read_manifest_from_archive",
            staticmethod(_boom),
        )
        with pytest.raises(RecursionError):
            await service.list_backups()

    def test_archive_match_swallows_unexpected_error(
        self,
        tmp_path: Path,
        archive_entry: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = _make_service(tmp_path)

        def _boom(_path: Path) -> object:
            msg = "unexpected probe failure"
            raise RuntimeError(msg)

        monkeypatch.setattr(
            BackupService,
            "_read_manifest_from_archive",
            staticmethod(_boom),
        )
        assert service._archive_matches_backup(archive_entry, "other") is False

    def test_archive_match_memory_error_propagates(
        self,
        tmp_path: Path,
        archive_entry: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = _make_service(tmp_path)

        def _boom(_path: Path) -> object:
            raise MemoryError

        monkeypatch.setattr(
            BackupService,
            "_read_manifest_from_archive",
            staticmethod(_boom),
        )
        with pytest.raises(MemoryError):
            service._archive_matches_backup(archive_entry, "other")

    def test_archive_match_recursion_error_propagates(
        self,
        tmp_path: Path,
        archive_entry: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = _make_service(tmp_path)

        def _boom(_path: Path) -> object:
            raise RecursionError

        monkeypatch.setattr(
            BackupService,
            "_read_manifest_from_archive",
            staticmethod(_boom),
        )
        with pytest.raises(RecursionError):
            service._archive_matches_backup(archive_entry, "other")
