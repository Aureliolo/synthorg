"""Backup event constants for structured logging.

Constants follow the ``backup.<entity>.<action>`` naming convention
and are passed as the first argument to ``logger.info()``/``logger.debug()``
calls in the backup layer.
"""

from typing import Final

# Backup lifecycle events
BACKUP_STARTED: Final[str] = "backup.backup.started"
BACKUP_COMPLETED: Final[str] = "backup.backup.completed"
BACKUP_FAILED: Final[str] = "backup.backup.failed"
BACKUP_IN_PROGRESS: Final[str] = "backup.backup.in_progress"

# Per-component events
BACKUP_COMPONENT_STARTED: Final[str] = "backup.component.started"
BACKUP_COMPONENT_COMPLETED: Final[str] = "backup.component.completed"
BACKUP_COMPONENT_FAILED: Final[str] = "backup.component.failed"

# Manifest events
BACKUP_MANIFEST_WRITTEN: Final[str] = "backup.manifest.written"
BACKUP_MANIFEST_INVALID: Final[str] = "backup.manifest.invalid"

# Retention events
BACKUP_RETENTION_PRUNED: Final[str] = "backup.retention.pruned"
BACKUP_RETENTION_FAILED: Final[str] = "backup.retention.failed"

# Scheduler events
BACKUP_SCHEDULER_STARTED: Final[str] = "backup.scheduler.started"
BACKUP_SCHEDULER_STOPPED: Final[str] = "backup.scheduler.stopped"
BACKUP_SCHEDULER_DORMANT: Final[str] = "backup.scheduler.dormant"
BACKUP_SCHEDULER_RESCHEDULED: Final[str] = "backup.scheduler.rescheduled"
BACKUP_SCHEDULER_TICK: Final[str] = "backup.scheduler.tick"

# Restore events
BACKUP_RESTORE_STARTED: Final[str] = "backup.restore.started"
BACKUP_RESTORE_COMPLETED: Final[str] = "backup.restore.completed"
BACKUP_RESTORE_FAILED: Final[str] = "backup.restore.failed"
BACKUP_RESTORE_ROLLBACK: Final[str] = "backup.restore.rollback"

# Management events
BACKUP_DELETED: Final[str] = "backup.backup.deleted"
BACKUP_LISTED: Final[str] = "backup.backup.listed"
BACKUP_NOT_FOUND: Final[str] = "backup.backup.not_found"

# MCP audit events
BACKUP_DELETED_VIA_MCP: Final[str] = "backup.backup.deleted_via_mcp"
BACKUP_RESTORE_TRIGGERED_VIA_MCP: Final[str] = "backup.restore.triggered_via_mcp"
