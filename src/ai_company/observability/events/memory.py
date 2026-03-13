"""Memory event constants for structured logging.

Constants follow the ``memory.<entity>.<action>`` naming convention
and are passed as the first argument to structured logger calls
(``logger.debug()``, ``logger.info()``, ``logger.warning()``,
``logger.error()``) in the memory layer.
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
MEMORY_BACKEND_CONFIG_INVALID: Final[str] = "memory.backend.config_invalid"
MEMORY_BACKEND_NOT_CONNECTED: Final[str] = "memory.backend.not_connected"
MEMORY_BACKEND_AGENT_ID_REJECTED: Final[str] = "memory.backend.agent_id_rejected"
MEMORY_BACKEND_SYSTEM_ERROR: Final[str] = "memory.backend.system_error"

# ── Entry operations ──────────────────────────────────────────────

MEMORY_ENTRY_STORED: Final[str] = "memory.entry.stored"
MEMORY_ENTRY_STORE_FAILED: Final[str] = "memory.entry.store_failed"
MEMORY_ENTRY_RETRIEVED: Final[str] = "memory.entry.retrieved"
MEMORY_ENTRY_RETRIEVAL_FAILED: Final[str] = "memory.entry.retrieval_failed"
MEMORY_ENTRY_DELETED: Final[str] = "memory.entry.deleted"
MEMORY_ENTRY_DELETE_FAILED: Final[str] = "memory.entry.delete_failed"
MEMORY_ENTRY_FETCHED: Final[str] = "memory.entry.fetched"
MEMORY_ENTRY_FETCH_FAILED: Final[str] = "memory.entry.fetch_failed"
MEMORY_ENTRY_COUNTED: Final[str] = "memory.entry.counted"
MEMORY_ENTRY_COUNT_FAILED: Final[str] = "memory.entry.count_failed"

# ── Shared knowledge ─────────────────────────────────────────────

MEMORY_SHARED_PUBLISHED: Final[str] = "memory.shared.published"
MEMORY_SHARED_PUBLISH_FAILED: Final[str] = "memory.shared.publish_failed"
MEMORY_SHARED_SEARCHED: Final[str] = "memory.shared.searched"
MEMORY_SHARED_SEARCH_FAILED: Final[str] = "memory.shared.search_failed"
MEMORY_SHARED_RETRACTED: Final[str] = "memory.shared.retracted"
MEMORY_SHARED_RETRACT_FAILED: Final[str] = "memory.shared.retract_failed"

# ── Validation ──────────────────────────────────────────────────

MEMORY_MODEL_INVALID: Final[str] = "memory.model.invalid"

# ── Retrieval pipeline ──────────────────────────────────────────

MEMORY_RETRIEVAL_START: Final[str] = "memory.retrieval.start"
MEMORY_RETRIEVAL_COMPLETE: Final[str] = "memory.retrieval.complete"
MEMORY_RETRIEVAL_DEGRADED: Final[str] = "memory.retrieval.degraded"
MEMORY_RETRIEVAL_SKIPPED: Final[str] = "memory.retrieval.skipped"
MEMORY_RANKING_COMPLETE: Final[str] = "memory.ranking.complete"
MEMORY_FORMAT_COMPLETE: Final[str] = "memory.format.complete"
MEMORY_FORMAT_INVALID_INJECTION_POINT: Final[str] = (
    "memory.format.invalid_injection_point"
)
MEMORY_TOKEN_BUDGET_EXCEEDED: Final[str] = "memory.token_budget.exceeded"  # noqa: S105

# ── Memory filter ──────────────────────────────────────────────

MEMORY_FILTER_INIT: Final[str] = "memory.filter.init"
MEMORY_FILTER_APPLIED: Final[str] = "memory.filter.applied"
MEMORY_FILTER_STORE_MISSING_TAG: Final[str] = "memory.filter.store_missing_tag"
