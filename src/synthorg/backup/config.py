"""Backup configuration models.

Frozen Pydantic models for backup scheduling, retention policy,
and component selection.
"""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.backup.models import BackupComponent
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)


class RetentionConfig(BaseModel):
    """Retention policy for automatic backup pruning.

    Attributes:
        max_count: Maximum number of backups to retain.
        max_age_days: Maximum age in days before pruning.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_count: int = Field(default=10, ge=1, le=1000)
    max_age_days: int = Field(default=30, ge=1, le=365)


class BackupConfig(BaseModel):
    """Top-level backup configuration.

    Attributes:
        enabled: Whether automatic backups are enabled.
        path: Directory path for storing backups.
        schedule_hours: Interval between scheduled backups in hours.
        retention: Retention policy configuration.
        on_shutdown: Whether to create a backup on graceful shutdown.
        on_startup: Whether to create a backup on startup.
        compression: Whether to compress backups as tar.gz archives.
        include: Components to include in backups.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    path: NotBlankStr = Field(
        default="/data/backups",
        description="Directory path for storing backups",
    )
    schedule_hours: int = Field(
        default=6,
        ge=1,
        le=168,
        description="Interval between scheduled backups in hours",
    )
    retention: RetentionConfig = Field(
        default_factory=RetentionConfig,
        description="Retention policy configuration",
    )
    on_shutdown: bool = Field(
        default=True,
        description="Create a backup on graceful shutdown",
    )
    on_startup: bool = Field(
        default=False,
        description=(
            "Create a backup on startup. CFG-1 audit: flipped from True"
            " to False -- scheduled backups (see ``schedule_hours``)"
            " provide the same guarantee without surprise writes at"
            " boot. Operators who want startup snapshots must opt in"
            " explicitly."
        ),
    )
    compression: bool = Field(
        default=True,
        description="Compress backups as tar.gz archives",
    )
    include: tuple[BackupComponent, ...] = Field(
        default=(
            BackupComponent.PERSISTENCE,
            BackupComponent.MEMORY,
            BackupComponent.CONFIG,
        ),
        description="Components to include in backups",
    )

    @model_validator(mode="after")
    def _reject_path_traversal(self) -> Self:
        """Reject parent-directory traversal to prevent path escapes."""
        parts = PureWindowsPath(self.path).parts + PurePosixPath(self.path).parts
        if ".." in parts:
            msg = "Backup path must not contain parent-directory traversal (..)"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="path",
                value=self.path,
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _reject_duplicate_components(self) -> Self:
        """Reject duplicate entries in the include tuple."""
        if len(self.include) != len(set(self.include)):
            msg = "Duplicate components in include"
            raise ValueError(msg)
        return self
