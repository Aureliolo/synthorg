"""Retention manager -- prune old backups by count and age."""

import asyncio
import json
import shutil
import tarfile
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.backup.errors import RetentionError
from synthorg.backup.models import BackupManifest, BackupTrigger
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.backup import (
    BACKUP_MANIFEST_INVALID,
    BACKUP_RETENTION_FAILED,
    BACKUP_RETENTION_PRUNED,
)

if TYPE_CHECKING:
    from pathlib import Path

    from synthorg.backup.config import RetentionConfig

logger = get_logger(__name__)


class RetentionManager:
    """Prune old backups based on count and age policies.

    The most recent backup and pre_migration backups are never
    pruned.

    Args:
        config: Retention policy configuration.
        backup_path: Root directory containing all backups.
    """

    def __init__(self, config: RetentionConfig, backup_path: Path) -> None:
        self._config = config
        self._backup_path = backup_path

    async def prune(self) -> tuple[str, ...]:
        """Remove backups that exceed count or age limits.

        The most recent backup and pre_migration backups are never
        pruned.

        Returns:
            Tuple of pruned backup IDs.

        Raises:
            RetentionError: If pruning fails.
        """
        try:
            manifests = await asyncio.to_thread(self._load_manifests)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.error(
                BACKUP_RETENTION_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to load manifests: {safe_error_description(exc)}"
            raise RetentionError(msg) from exc

        if not manifests:
            return ()

        # Sort by timestamp descending (newest first)
        manifests.sort(key=lambda m: m.timestamp, reverse=True)
        candidates = self._identify_prunable(manifests)
        return await asyncio.to_thread(self._execute_prune, candidates)

    def _identify_prunable(
        self,
        manifests: list[BackupManifest],
    ) -> list[BackupManifest]:
        """Identify manifests eligible for pruning."""
        now = datetime.now(UTC)
        max_age = timedelta(days=self._config.max_age_days)
        candidates: list[BackupManifest] = []

        for i, manifest in enumerate(manifests):
            if i == 0:
                continue
            if manifest.trigger == BackupTrigger.PRE_MIGRATION:
                continue
            if self._should_prune(i, manifest, now, max_age):
                candidates.append(manifest)

        return candidates

    def _should_prune(
        self,
        index: int,
        manifest: BackupManifest,
        now: datetime,
        max_age: timedelta,
    ) -> bool:
        """Determine if a single manifest should be pruned."""
        if index >= self._config.max_count:
            return True
        try:
            backup_time = datetime.fromisoformat(manifest.timestamp)
            if now - backup_time > max_age:
                return True
        except ValueError:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                backup_id=manifest.backup_id,
                error="Invalid timestamp format",
                timestamp=manifest.timestamp,
            )
        return False

    def _execute_prune(
        self,
        candidates: list[BackupManifest],
    ) -> tuple[str, ...]:
        """Delete candidate backups and return pruned IDs."""
        pruned: list[str] = []
        for manifest in candidates:
            try:
                deleted = self._delete_backup(manifest.backup_id)
                if deleted:
                    pruned.append(manifest.backup_id)
                    logger.info(
                        BACKUP_RETENTION_PRUNED,
                        backup_id=manifest.backup_id,
                        trigger=manifest.trigger.value,
                        timestamp=manifest.timestamp,
                    )
                else:
                    logger.warning(
                        BACKUP_RETENTION_FAILED,
                        backup_id=manifest.backup_id,
                        error="Backup not found for deletion",
                    )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.error(
                    BACKUP_RETENTION_FAILED,
                    backup_id=manifest.backup_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        return tuple(pruned)

    def _load_manifests(self) -> list[BackupManifest]:
        """Load all manifest.json files from the backup directory."""
        if not self._backup_path.exists():
            return []

        manifests: list[BackupManifest] = []
        for entry in self._backup_path.iterdir():
            if entry.is_dir():
                result = self._load_dir_manifest(entry)
                if result is not None:
                    manifests.append(result)
            elif entry.suffix == ".gz" and entry.stem.endswith(".tar"):
                result = self._load_archive_manifest(entry)
                if result is not None:
                    manifests.append(result)

        return manifests

    @staticmethod
    def _load_dir_manifest(entry: Path) -> BackupManifest | None:
        """Load a manifest from a backup directory."""
        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return BackupManifest.model_validate(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(manifest_path),
                category="json_parse_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        except ValidationError as exc:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(manifest_path),
                category="schema_validation_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        except OSError as exc:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(manifest_path),
                category="io_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    @staticmethod
    def _load_archive_manifest(entry: Path) -> BackupManifest | None:  # noqa: PLR0911
        """Load a manifest from a compressed tar.gz archive."""
        try:
            with tarfile.open(entry, "r:gz") as tar:
                try:
                    member = tar.getmember("manifest.json")
                except KeyError:
                    return None
                extracted = tar.extractfile(member)
                if extracted is None:
                    return None
                with extracted as f:
                    raw = f.read()
                data = json.loads(raw)
                return BackupManifest.model_validate(data)
        except MemoryError, RecursionError:
            raise
        except tarfile.TarError as exc:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(entry),
                category="archive_corrupt",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(entry),
                category="json_parse_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        except ValidationError as exc:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(entry),
                category="schema_validation_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        except OSError as exc:
            logger.warning(
                BACKUP_MANIFEST_INVALID,
                path=str(entry),
                category="io_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    def _delete_backup(self, backup_id: str) -> bool:
        """Delete a backup directory or archive by ID.

        Returns:
            True if the backup was found and deleted.
        """
        for entry in self._backup_path.iterdir():
            if entry.is_dir() and entry.name.startswith(f"{backup_id}_"):
                shutil.rmtree(entry)
                return True
            if (
                entry.is_file()
                and entry.name.startswith(f"{backup_id}_")
                and entry.name.endswith(".tar.gz")
            ):
                entry.unlink()
                return True

        # Also check if backup_id matches the manifest inside directories
        for entry in self._backup_path.iterdir():
            if not entry.is_dir():
                continue
            manifest_path = entry / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if data.get("backup_id") == backup_id:
                    shutil.rmtree(entry)
                    return True
            except Exception:
                logger.warning(
                    BACKUP_MANIFEST_INVALID,
                    path=str(manifest_path),
                )
        return False
