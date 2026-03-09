"""Memory consolidation event constants for structured logging.

Constants follow the ``consolidation.<entity>.<action>`` naming convention.
"""

from typing import Final

# ── Maintenance orchestration ─────────────────────────────────────

MAINTENANCE_START: Final[str] = "consolidation.maintenance.start"
MAINTENANCE_COMPLETE: Final[str] = "consolidation.maintenance.complete"
MAINTENANCE_FAILED: Final[str] = "consolidation.maintenance.failed"

# ── Consolidation operations ──────────────────────────────────────

CONSOLIDATION_START: Final[str] = "consolidation.run.start"
CONSOLIDATION_COMPLETE: Final[str] = "consolidation.run.complete"
CONSOLIDATION_FAILED: Final[str] = "consolidation.run.failed"
CONSOLIDATION_SKIPPED: Final[str] = "consolidation.run.skipped"

# ── Strategy operations ──────────────────────────────────────────

STRATEGY_START: Final[str] = "consolidation.strategy.start"
STRATEGY_COMPLETE: Final[str] = "consolidation.strategy.complete"

# ── Retention cleanup ────────────────────────────────────────────

RETENTION_CLEANUP_START: Final[str] = "consolidation.retention.start"
RETENTION_CLEANUP_COMPLETE: Final[str] = "consolidation.retention.complete"
RETENTION_CLEANUP_FAILED: Final[str] = "consolidation.retention.failed"
RETENTION_DELETE_SKIPPED: Final[str] = "consolidation.retention.delete_skipped"

# ── Archival operations ──────────────────────────────────────────

ARCHIVAL_ENTRY_STORED: Final[str] = "consolidation.archival.stored"
ARCHIVAL_SEARCH_COMPLETE: Final[str] = "consolidation.archival.search_complete"
ARCHIVAL_FAILED: Final[str] = "consolidation.archival.failed"

# ── Max memories enforcement ─────────────────────────────────────

MAX_MEMORIES_ENFORCED: Final[str] = "consolidation.max_memories.enforced"
MAX_MEMORIES_ENFORCE_FAILED: Final[str] = "consolidation.max_memories.failed"
