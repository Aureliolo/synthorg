"""Org memory event constants for structured logging.

Constants follow the ``org_memory.<entity>.<action>`` naming convention.
"""

from typing import Final

# ── Query operations ──────────────────────────────────────────────

ORG_MEMORY_QUERY_START: Final[str] = "org_memory.query.start"
ORG_MEMORY_QUERY_COMPLETE: Final[str] = "org_memory.query.complete"
ORG_MEMORY_QUERY_FAILED: Final[str] = "org_memory.query.failed"

# ── Write operations ─────────────────────────────────────────────

ORG_MEMORY_WRITE_START: Final[str] = "org_memory.write.start"
ORG_MEMORY_WRITE_COMPLETE: Final[str] = "org_memory.write.complete"
ORG_MEMORY_WRITE_DENIED: Final[str] = "org_memory.write.denied"
ORG_MEMORY_WRITE_FAILED: Final[str] = "org_memory.write.failed"

# ── Policy listing ───────────────────────────────────────────────

ORG_MEMORY_POLICIES_LISTED: Final[str] = "org_memory.policies.listed"

# ── Backend lifecycle ────────────────────────────────────────────

ORG_MEMORY_BACKEND_CONNECTED: Final[str] = "org_memory.backend.connected"
ORG_MEMORY_BACKEND_DISCONNECTED: Final[str] = "org_memory.backend.disconnected"
ORG_MEMORY_CONNECT_FAILED: Final[str] = "org_memory.store.connect_failed"
ORG_MEMORY_DISCONNECT_FAILED: Final[str] = "org_memory.store.disconnect_failed"
ORG_MEMORY_NOT_CONNECTED: Final[str] = "org_memory.store.not_connected"
ORG_MEMORY_ROW_PARSE_FAILED: Final[str] = "org_memory.store.row_parse_failed"

# ── Model validation ────────────────────────────────────────────

ORG_MEMORY_MODEL_INVALID: Final[str] = "org_memory.model.invalid"

# ── MVCC operations ─────────────────────────────────────────────

ORG_MEMORY_MVCC_PUBLISH_APPENDED: Final[str] = "org_memory.mvcc.publish_appended"
ORG_MEMORY_MVCC_RETRACT_APPENDED: Final[str] = "org_memory.mvcc.retract_appended"
ORG_MEMORY_MVCC_SNAPSHOT_AT_QUERIED: Final[str] = "org_memory.mvcc.snapshot_at_queried"
ORG_MEMORY_MVCC_LOG_QUERIED: Final[str] = "org_memory.mvcc.log_queried"
