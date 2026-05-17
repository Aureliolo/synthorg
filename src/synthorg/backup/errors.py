"""Backup error hierarchy.

All backup-related errors inherit from :class:`BackupError` (which is
itself a :class:`DomainError` subclass) so callers can catch the entire
family with a single except clause and the API layer maps every
subclass to a structured RFC 9457 response via the centralised
``handle_domain_error`` dispatch.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class BackupError(DomainError):
    """Base exception for all backup operations."""

    default_message: ClassVar[str] = "Backup operation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class BackupInProgressError(BackupError):
    """Raised when a backup is attempted while another is in progress."""

    default_message: ClassVar[str] = "Backup operation already in progress"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.BACKUP_IN_PROGRESS
    status_code: ClassVar[int] = 409


class RestoreError(BackupError):
    """Raised when a restore operation fails."""

    default_message: ClassVar[str] = "Restore operation failed"
    error_code: ClassVar[ErrorCode] = ErrorCode.BACKUP_RESTORE_FAILED


class ManifestError(BackupError):
    """Raised when a backup manifest is invalid or corrupt."""

    default_message: ClassVar[str] = "Backup manifest is invalid or corrupt"
    error_code: ClassVar[ErrorCode] = ErrorCode.BACKUP_MANIFEST_ERROR


class ComponentBackupError(BackupError):
    """Raised when a per-component backup or restore step fails."""


class RetentionError(BackupError):
    """Raised when backup pruning fails."""


class BackupNotFoundError(BackupError):
    """Raised when a requested backup ID does not exist."""

    default_message: ClassVar[str] = "Backup not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.BACKUP_NOT_FOUND
    status_code: ClassVar[int] = 404


class BackupUnrestartableError(BackupError):
    """Raised when ``BackupScheduler.start()`` is called after a timed-out stop.

    The scheduler refuses to spawn a fresh loop on top of an orphan
    task that may still own the backup lock, so the request is
    rejected with HTTP 409.
    """

    default_message: ClassVar[str] = (
        "Backup scheduler is unrestartable after a timed-out stop"
    )
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.BACKUP_UNRESTARTABLE
    status_code: ClassVar[int] = 409


class BackupConfigurationError(BackupError):
    """Raised when a backup handler cannot be built from configuration.

    Typical cause: the configured persistence backend implies a handler
    that needs additional configuration (e.g. ``persistence.backend ==
    "postgres"`` but ``persistence.postgres`` is unset). Surfaced at
    build time so the misconfiguration fails fast rather than at the
    first scheduled backup.
    """
