"""Domain models for the backup system.

Includes enumerations, manifest, info summaries, and
request/response models for the restore workflow.
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.core.types import NotBlankStr

_BACKUP_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_CHECKSUM_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class BackupTrigger(StrEnum):
    """What initiated the backup."""

    SCHEDULED = "scheduled"
    MANUAL = "manual"
    SHUTDOWN = "shutdown"
    STARTUP = "startup"
    PRE_MIGRATION = "pre_migration"


class BackupComponent(StrEnum):
    """Identifiers for independently-backed-up data components."""

    PERSISTENCE = "persistence"
    MEMORY = "memory"
    CONFIG = "config"


class BackupManifest(BaseModel):
    """Full manifest written alongside each backup.

    Serialised to ``manifest.json`` inside the backup directory
    or archive.

    Attributes:
        synthorg_version: SynthOrg application version at backup time.
        timestamp: ISO 8601 timestamp of backup creation.
        trigger: What initiated the backup.
        components: Components included in this backup.
        size_bytes: Total backup size in bytes.
        checksum: SHA-256 checksum of backup contents (``sha256:<hex>``).
        backup_id: Unique identifier for this backup (12-char hex).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    synthorg_version: NotBlankStr
    timestamp: NotBlankStr
    trigger: BackupTrigger
    components: tuple[BackupComponent, ...]
    size_bytes: int = Field(ge=0)
    checksum: NotBlankStr
    backup_id: NotBlankStr

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, v: str) -> str:
        """Reject timestamps that are not valid ISO 8601."""
        try:
            datetime.fromisoformat(v)
        except ValueError as exc:
            msg = f"Invalid ISO 8601 timestamp: {v}"
            raise ValueError(msg) from exc
        return v

    @field_validator("checksum")
    @classmethod
    def _validate_checksum(cls, v: str) -> str:
        """Validate checksum format is ``sha256:<64-hex-chars>``."""
        if not _CHECKSUM_RE.match(v):
            msg = f"Checksum must match sha256:<64-hex-chars>, got: {v}"
            raise ValueError(msg)
        return v


class BackupInfo(BaseModel):
    """Lightweight backup summary for list endpoints.

    Attributes:
        backup_id: Unique identifier for this backup.
        timestamp: ISO 8601 timestamp of backup creation.
        trigger: What initiated the backup.
        components: Components included in this backup.
        size_bytes: Total backup size in bytes.
        compressed: Whether the backup is compressed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    backup_id: NotBlankStr
    timestamp: NotBlankStr
    trigger: BackupTrigger
    components: tuple[BackupComponent, ...]
    size_bytes: int = Field(ge=0)
    compressed: bool

    @classmethod
    def from_manifest(cls, manifest: BackupManifest, *, compressed: bool) -> Self:
        """Create a BackupInfo from a BackupManifest.

        Args:
            manifest: Source manifest.
            compressed: Whether the backup is compressed.

        Returns:
            New BackupInfo instance.
        """
        return cls(
            backup_id=manifest.backup_id,
            timestamp=manifest.timestamp,
            trigger=manifest.trigger,
            components=manifest.components,
            size_bytes=manifest.size_bytes,
            compressed=compressed,
        )


class RestoreRequest(BaseModel):
    """Request body for initiating a restore operation.

    The ``confirm`` safety gate is enforced at the controller/service
    boundary, not at the model level.

    Attributes:
        backup_id: Which backup to restore from (12-char hex).
        components: Components to restore (``None`` = all from manifest).
        confirm: Safety gate -- must be ``True`` to proceed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    backup_id: NotBlankStr
    components: tuple[BackupComponent, ...] | None = None
    confirm: bool = False

    @model_validator(mode="after")
    def _validate_backup_id_format(self) -> Self:
        """Reject backup IDs that don't match the expected hex format."""
        if not _BACKUP_ID_RE.match(self.backup_id):
            msg = (
                f"backup_id must be a 12-character hex string, got: {self.backup_id!r}"
            )
            raise ValueError(msg)
        return self


class RestoreResponse(BaseModel):
    """Response after a successful restore operation.

    Attributes:
        manifest: Manifest of the restored backup.
        restored_components: Components that were restored.
        safety_backup_id: ID of the pre-restore safety backup.
        restart_required: Whether the application must be restarted.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    manifest: BackupManifest
    restored_components: tuple[BackupComponent, ...]
    safety_backup_id: NotBlankStr
    restart_required: bool = True
