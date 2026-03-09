"""Memory event constants for structured logging.

Constants follow the ``memory.<entity>.<action>`` naming convention
and are passed as the first argument to ``logger.info()``/``logger.debug()``
calls in the memory layer.
"""

from typing import Final

# ── Backend lifecycle ──────────────────────────────────────────────

MEMORY_BACKEND_CONNECTING: Final[str] = "memory.backend.connecting"
MEMORY_BACKEND_CONNECTED: Final[str] = "memory.backend.connected"
MEMORY_BACKEND_CONNECTION_FAILED: Final[str] = "memory.backend.connection_failed"
MEMORY_BACKEND_DISCONNECTING: Final[str] = "memory.backend.disconnecting"
MEMORY_BACKEND_DISCONNECTED: Final[str] = "memory.backend.disconnected"
MEMORY_BACKEND_HEALTH_CHECK: Final[str] = "memory.backend.health_check"
MEMORY_BACKEND_CREATED: Final[str] = "memory.backend.created"
MEMORY_BACKEND_UNKNOWN: Final[str] = "memory.backend.unknown"
MEMORY_BACKEND_NOT_CONNECTED: Final[str] = "memory.backend.not_connected"

# ── Entry operations ──────────────────────────────────────────────

MEMORY_ENTRY_STORED: Final[str] = "memory.entry.stored"
MEMORY_ENTRY_STORE_FAILED: Final[str] = "memory.entry.store_failed"
MEMORY_ENTRY_RETRIEVED: Final[str] = "memory.entry.retrieved"
MEMORY_ENTRY_RETRIEVAL_FAILED: Final[str] = "memory.entry.retrieval_failed"
MEMORY_ENTRY_DELETED: Final[str] = "memory.entry.deleted"
MEMORY_ENTRY_DELETE_FAILED: Final[str] = "memory.entry.delete_failed"
MEMORY_ENTRY_COUNTED: Final[str] = "memory.entry.counted"
MEMORY_ENTRY_COUNT_FAILED: Final[str] = "memory.entry.count_failed"

# ── Shared knowledge ─────────────────────────────────────────────

MEMORY_SHARED_PUBLISHED: Final[str] = "memory.shared.published"
MEMORY_SHARED_PUBLISH_FAILED: Final[str] = "memory.shared.publish_failed"
MEMORY_SHARED_SEARCHED: Final[str] = "memory.shared.searched"
MEMORY_SHARED_SEARCH_FAILED: Final[str] = "memory.shared.search_failed"
MEMORY_SHARED_RETRACTED: Final[str] = "memory.shared.retracted"
MEMORY_SHARED_RETRACT_FAILED: Final[str] = "memory.shared.retract_failed"

# ── Capability checks ────────────────────────────────────────────

MEMORY_CAPABILITY_UNSUPPORTED: Final[str] = "memory.capability.unsupported"
