"""Backup error hierarchy.

All backup-related errors inherit from ``BackupError`` so callers
can catch the entire family with a single except clause.
"""

from typing import ClassVar

from synthorg.core.domain_errors import ConflictError


class BackupError(Exception):
    """Base exception for all backup operations."""


class BackupInProgressError(BackupError):
    """Raised when a backup is attempted while another is in progress."""


class RestoreError(BackupError):
    """Raised when a restore operation fails."""


class ManifestError(BackupError):
    """Raised when a backup manifest is invalid or corrupt."""


class ComponentBackupError(BackupError):
    """Raised when a per-component backup or restore step fails."""


class RetentionError(BackupError):
    """Raised when backup pruning fails."""


class BackupNotFoundError(BackupError):
    """Raised when a requested backup ID does not exist."""


class BackupUnrestartableError(ConflictError):
    """Raised when ``BackupScheduler.start()`` is called after a timed-out stop.

    The scheduler refuses to spawn a fresh loop on top of an orphan
    task that may still own the backup lock, so the request is
    rejected with HTTP 409. Inherits :class:`ConflictError` so the
    centralised ``EXCEPTION_HANDLERS`` routing produces the right
    RFC 9457 response.
    """

    default_message: ClassVar[str] = (
        "Backup scheduler is unrestartable after a timed-out stop"
    )
