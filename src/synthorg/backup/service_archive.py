"""Archive/manifest helpers for :class:`BackupService`.

Extracted from :mod:`synthorg.backup.service` to keep the main module
under the project size limit.
"""

import asyncio
import hashlib
import json
import shutil
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.backup.errors import (
    BackupInProgressError,
    BackupNotFoundError,
    ManifestError,
)
from synthorg.backup.models import BackupInfo, BackupManifest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.backup import (
    BACKUP_DELETED,
    BACKUP_FAILED,
    BACKUP_IN_PROGRESS,
    BACKUP_LISTED,
    BACKUP_MANIFEST_INVALID,
    BACKUP_NOT_FOUND,
)

if TYPE_CHECKING:
    from synthorg.backup.config import BackupConfig

logger = get_logger(__name__)

_CHECKSUM_CHUNK_SIZE = 65536
# Internal constant by design: defensive cap on serialised backup
# manifest size; prevents manifest bloat from runaway tags or notes.
# Not exposed to the settings registry.
_MANIFEST_MAX_SIZE = 65536


class BackupServiceArchiveMixin:
    """Mixin providing archive/manifest/listing helpers."""

    _backup_path: Path
    _backup_lock: asyncio.Lock
    _config: BackupConfig

    async def list_backups(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[BackupInfo, ...]:
        """List available backups, newest first.

        When ``limit`` is provided, at most ``limit`` entries are
        returned starting at ``offset``.  Manifest parsing is cheap
        per-entry but linear in directory size, so pushing pagination
        down here lets the controller stay O(limit) instead of O(total
        backups) -- important when the on-disk history grows.

        Args:
            limit: Maximum number of entries to return.  ``None`` keeps
                the legacy behaviour of listing every entry.
            offset: Number of entries to skip from the newest.

        Returns:
            Tuple of backup info summaries, newest first.
        """
        entries = await asyncio.to_thread(self._scan_backup_entries)
        if entries is None:
            logger.debug(BACKUP_LISTED, count=0)
            return ()

        # The directory listing order is filesystem-dependent, so
        # sort the cheap entry handles first and only then parse the
        # window the caller asked for.  Entry names encode the backup
        # timestamp (``backup-YYYYMMDD-HHMMSS...``) which is a stable
        # proxy for the ``BackupInfo.timestamp`` we eventually return.
        entries.sort(key=lambda item: item[0].name, reverse=True)

        start = max(0, offset)
        stop = start + limit if limit is not None else None
        window = entries[start:stop] if stop is not None else entries[start:]

        infos: list[BackupInfo] = []
        for entry, is_dir in window:
            if is_dir:
                info = await asyncio.to_thread(self._try_load_dir_info, entry)
                if info is not None:
                    infos.append(info)
            elif entry.name.endswith(".tar.gz"):
                info = await self._try_load_archive_info(entry)
                if info is not None:
                    infos.append(info)

        # Final tie-break by the parsed timestamp so entries with the
        # same filename prefix end up in true chronological order.
        infos.sort(key=lambda i: i.timestamp, reverse=True)
        logger.debug(BACKUP_LISTED, count=len(infos))
        return tuple(infos)

    def _scan_backup_entries(
        self,
    ) -> list[tuple[Path, bool]] | None:
        """Return ``(entry, is_dir)`` pairs or ``None`` if the dir is absent."""
        if not self._backup_path.exists():
            return None
        return [(entry, entry.is_dir()) for entry in self._backup_path.iterdir()]

    def _try_load_dir_info(
        self,
        entry: Path,
    ) -> BackupInfo | None:
        """Try to load backup info from a directory manifest."""
        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            m = BackupManifest.model_validate(data)
            return BackupInfo.from_manifest(m, compressed=False)
        except Exception:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(manifest_path),
                exc_info=True,
            )
            return None

    def _load_dir_manifest_matching(
        self,
        entry: Path,
        backup_id: str,
    ) -> BackupManifest | None:
        """Load and validate a directory manifest matching ``backup_id``."""
        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            m = BackupManifest.model_validate(data)
        except Exception as exc:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(manifest_path),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        return m if m.backup_id == backup_id else None

    async def _try_load_archive_info(
        self,
        entry: Path,
    ) -> BackupInfo | None:
        """Try to load backup info from a compressed archive."""
        try:
            m = await asyncio.to_thread(
                self._read_manifest_from_archive,
                entry,
            )
            if m is not None:
                return BackupInfo.from_manifest(m, compressed=True)
        except Exception:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(entry),
                exc_info=True,
            )
        return None

    async def get_backup(self, backup_id: str) -> BackupManifest:
        """Get the full manifest for a specific backup."""
        from synthorg.backup.service import _validate_backup_id  # noqa: PLC0415

        _validate_backup_id(backup_id)
        return await self._load_manifest(backup_id)

    async def delete_backup(self, backup_id: str) -> None:
        """Delete a backup by ID.

        Serialized via ``_backup_lock`` so a concurrent create/restore
        cannot observe a half-deleted directory or archive.
        """
        from synthorg.backup.service import _validate_backup_id  # noqa: PLC0415

        _validate_backup_id(backup_id)

        if self._backup_lock.locked():
            logger.warning(BACKUP_IN_PROGRESS, backup_id=backup_id)
            msg = "A backup or restore is already in progress"
            raise BackupInProgressError(msg)

        async with self._backup_lock:
            deleted = await asyncio.to_thread(self._try_delete_backup, backup_id)

        if not deleted:
            logger.warning(BACKUP_NOT_FOUND, backup_id=backup_id)
            msg = f"Backup not found: {backup_id}"
            raise BackupNotFoundError(msg)

        logger.info(BACKUP_DELETED, backup_id=backup_id)

    def _try_delete_backup(self, backup_id: str) -> bool:
        """Attempt to delete a backup, returning True on success."""
        if not self._backup_path.exists():
            return False

        for entry in self._backup_path.iterdir():
            if entry.is_dir():
                if self._dir_matches_backup(entry, backup_id):
                    shutil.rmtree(entry)
                    return True
            elif (
                entry.is_file()
                and entry.name.endswith(".tar.gz")
                and entry.name.startswith(f"{backup_id}_")
            ):
                entry.unlink()
                return True
        return False

    def _dir_matches_backup(self, entry: Path, backup_id: str) -> bool:
        """Check if a directory contains a manifest matching backup_id."""
        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            return False
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return bool(data.get("backup_id") == backup_id)
        except Exception:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(manifest_path),
            )
            return False

    async def _load_manifest(self, backup_id: str) -> BackupManifest:
        """Load manifest for a given backup ID."""
        entries = await asyncio.to_thread(self._scan_backup_entries)
        if entries is not None:
            for entry, is_dir in entries:
                result = await self._try_load_entry_manifest(
                    entry,
                    backup_id,
                    is_dir=is_dir,
                )
                if result is not None:
                    return result

        logger.warning(BACKUP_NOT_FOUND, backup_id=backup_id)
        msg = f"Backup not found: {backup_id}"
        raise BackupNotFoundError(msg)

    async def _try_load_entry_manifest(
        self,
        entry: Path,
        backup_id: str,
        *,
        is_dir: bool,
    ) -> BackupManifest | None:
        """Try to load a manifest matching backup_id."""
        if is_dir:
            return await asyncio.to_thread(
                self._load_dir_manifest_matching,
                entry,
                backup_id,
            )
        if entry.name.endswith(".tar.gz"):
            try:
                archive_manifest = await asyncio.to_thread(
                    self._read_manifest_from_archive,
                    entry,
                )
                if (
                    archive_manifest is not None
                    and archive_manifest.backup_id == backup_id
                ):
                    return archive_manifest
            except Exception:
                logger.warning(
                    BACKUP_MANIFEST_INVALID,
                    path=str(entry),
                    exc_info=True,
                )
        return None

    async def _find_backup_dir(self, backup_id: str) -> Path | None:
        """Find uncompressed backup directory by ID."""
        return await asyncio.to_thread(self._find_backup_dir_sync, backup_id)

    def _find_backup_dir_sync(self, backup_id: str) -> Path | None:
        """Synchronous filesystem scan for the backup directory."""
        if not self._backup_path.exists():
            return None
        for entry in self._backup_path.iterdir():
            if not entry.is_dir():
                continue
            if self._dir_matches_backup(entry, backup_id):
                return entry
        return None

    async def _extract_archive(self, backup_id: str) -> Path | None:
        """Extract a compressed backup archive to a temp directory."""
        entries = await asyncio.to_thread(self._scan_backup_entries)
        if entries is None:
            return None
        for entry, _is_dir in entries:
            if not entry.name.endswith(".tar.gz"):
                continue
            matches = await asyncio.to_thread(
                self._archive_matches_backup,
                entry,
                backup_id,
            )
            if not matches:
                continue

            temp_dir = self._backup_path / f"_restore_{backup_id}"
            try:
                await asyncio.to_thread(
                    self._extract_tar,
                    entry,
                    temp_dir,
                )
            except ManifestError:
                raise
            except Exception:
                logger.warning(
                    BACKUP_FAILED,
                    backup_id=backup_id,
                    error="Failed to extract archive",
                    exc_info=True,
                )
                if await asyncio.to_thread(temp_dir.exists):
                    await asyncio.to_thread(shutil.rmtree, temp_dir)
                msg = f"Failed to extract archive for backup: {backup_id}"
                raise ManifestError(msg) from None
            else:
                return temp_dir
        return None

    def _archive_matches_backup(
        self,
        entry: Path,
        backup_id: str,
    ) -> bool:
        """Check if an archive contains a manifest matching backup_id."""
        if entry.name.startswith(f"{backup_id}_"):
            return True
        try:
            m = self._read_manifest_from_archive(entry)
        except Exception:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(entry),
                error="Failed to read archive manifest for matching",
            )
            return False
        else:
            return m is not None and m.backup_id == backup_id

    @staticmethod
    def _compute_checksum(directory: Path) -> str:
        """Compute SHA-256 checksum of all files in a directory."""
        hasher = hashlib.sha256()
        for filepath in sorted(directory.rglob("*")):
            if (
                filepath.is_file()
                and not filepath.is_symlink()
                and filepath.name != "manifest.json"
            ):
                rel = filepath.relative_to(directory).as_posix()
                hasher.update(rel.encode("utf-8"))
                with filepath.open("rb") as fh:
                    while chunk := fh.read(_CHECKSUM_CHUNK_SIZE):
                        hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def _compress_dir(source_dir: Path, archive_path: Path) -> None:
        """Create a tar.gz archive from a directory."""
        with tarfile.open(archive_path, "w:gz") as tar:
            for item in source_dir.iterdir():
                tar.add(item, arcname=item.name)

    @staticmethod
    def _extract_tar(archive_path: Path, target_dir: Path) -> None:
        """Extract a tar.gz archive to a target directory."""
        target_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    msg = f"Unsafe path in archive: {member.name}"
                    raise ManifestError(msg)
                if member.issym() or member.islnk():
                    linkname = member.linkname
                    if linkname.startswith("/") or ".." in Path(linkname).parts:
                        msg = (
                            f"Unsafe symlink target in archive: "
                            f"{member.name} -> {linkname}"
                        )
                        raise ManifestError(msg)
            tar.extractall(target_dir, filter="data")

    @staticmethod
    def _read_manifest_from_archive(
        archive_path: Path,
    ) -> BackupManifest | None:
        """Read manifest.json from a tar.gz archive."""
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                try:
                    member = tar.getmember("manifest.json")
                except KeyError:
                    return None
                f = tar.extractfile(member)
                if f is None:
                    return None
                raw = f.read(_MANIFEST_MAX_SIZE + 1)
                if len(raw) > _MANIFEST_MAX_SIZE:
                    logger.warning(
                        BACKUP_MANIFEST_INVALID,
                        path=str(archive_path),
                        error="manifest.json exceeds size limit",
                        read_bytes=len(raw),
                        max_bytes=_MANIFEST_MAX_SIZE,
                        member=member.name,
                    )
                    return None
                data = json.loads(raw)
                return BackupManifest.model_validate(data)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(archive_path),
                exc_info=True,
            )
            return None
