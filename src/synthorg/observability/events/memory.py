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
MEMORY_RRF_FUSION_COMPLETE: Final[str] = "memory.ranking.rrf_fusion_complete"
MEMORY_RRF_VALIDATION_FAILED: Final[str] = "memory.ranking.rrf_validation_failed"
MEMORY_FORMAT_COMPLETE: Final[str] = "memory.format.complete"
MEMORY_FORMAT_INVALID_INJECTION_POINT: Final[str] = (
    "memory.format.invalid_injection_point"
)
MEMORY_TOKEN_BUDGET_EXCEEDED: Final[str] = "memory.token_budget.exceeded"  # noqa: S105

# ── Memory filter ──────────────────────────────────────────────

MEMORY_FILTER_INIT: Final[str] = "memory.filter.init"
MEMORY_FILTER_APPLIED: Final[str] = "memory.filter.applied"
MEMORY_FILTER_STORE_MISSING_TAG: Final[str] = "memory.filter.store_missing_tag"

# ── Embedding selection ──────────────────────────────────────────

MEMORY_EMBEDDER_AUTO_SELECTED: Final[str] = "memory.embedder.auto_selected"
MEMORY_EMBEDDER_AUTO_SELECT_FAILED: Final[str] = "memory.embedder.auto_select_failed"
MEMORY_EMBEDDER_CHECKPOINT_ACTIVE: Final[str] = "memory.embedder.checkpoint_active"
MEMORY_EMBEDDER_CHECKPOINT_MISSING: Final[str] = "memory.embedder.checkpoint_missing"

# ── Fine-tuning pipeline ─────────────────────────────────────────

MEMORY_FINE_TUNE_REQUESTED: Final[str] = "memory.fine_tune.requested"
MEMORY_FINE_TUNE_VALIDATION_FAILED: Final[str] = "memory.fine_tune.validation_failed"
MEMORY_FINE_TUNE_STARTED: Final[str] = "memory.fine_tune.started"
MEMORY_FINE_TUNE_STAGE_ENTERED: Final[str] = "memory.fine_tune.stage_entered"
MEMORY_FINE_TUNE_PROGRESS: Final[str] = "memory.fine_tune.progress"
MEMORY_FINE_TUNE_COMPLETED: Final[str] = "memory.fine_tune.completed"
MEMORY_FINE_TUNE_FAILED: Final[str] = "memory.fine_tune.failed"
MEMORY_FINE_TUNE_CANCELLED: Final[str] = "memory.fine_tune.cancelled"
MEMORY_FINE_TUNE_BACKEND_UNSUPPORTED: Final[str] = (
    "memory.fine_tune.backend_unsupported"
)
MEMORY_FINE_TUNE_INVALID_REQUEST: Final[str] = "memory.fine_tune.invalid_request"
MEMORY_FINE_TUNE_INTERRUPTED: Final[str] = "memory.fine_tune.interrupted"
MEMORY_FINE_TUNE_DEPENDENCY_MISSING: Final[str] = "memory.fine_tune.dependency_missing"
MEMORY_FINE_TUNE_CHECKPOINT_SAVED: Final[str] = "memory.fine_tune.checkpoint_saved"
MEMORY_FINE_TUNE_CHECKPOINT_DEPLOYED: Final[str] = (
    "memory.fine_tune.checkpoint_deployed"
)
MEMORY_FINE_TUNE_CHECKPOINT_ROLLED_BACK: Final[str] = (
    "memory.fine_tune.checkpoint_rolled_back"
)
MEMORY_FINE_TUNE_CHECKPOINT_DELETED: Final[str] = "memory.fine_tune.checkpoint_deleted"
MEMORY_FINE_TUNE_PREFLIGHT_COMPLETED: Final[str] = (
    "memory.fine_tune.preflight_completed"
)
MEMORY_FINE_TUNE_BATCH_SIZE_RECOMMENDATION_FAILED: Final[str] = (
    "memory.fine_tune.batch_size_recommendation_failed"
)
MEMORY_FINE_TUNE_EVAL_COMPLETED: Final[str] = "memory.fine_tune.eval_completed"
MEMORY_FINE_TUNE_BACKUP_READ_SKIPPED: Final[str] = (
    "memory.fine_tune.backup_read_skipped"
)
MEMORY_FINE_TUNE_WS_EMIT_FAILED: Final[str] = "memory.fine_tune.ws_emit_failed"
MEMORY_FINE_TUNE_PERSIST_FAILED: Final[str] = "memory.fine_tune.persist_failed"
MEMORY_EMBEDDER_SETTINGS_READ_FAILED: Final[str] = (
    "memory.embedder.settings_read_failed"
)
MEMORY_CHECKPOINT_DEPLOYED: Final[str] = "memory.checkpoint.deployed"
MEMORY_CHECKPOINT_DEPLOY_FAILED: Final[str] = "memory.checkpoint.deploy_failed"
MEMORY_CHECKPOINT_NOT_FOUND: Final[str] = "memory.checkpoint.not_found"
MEMORY_CHECKPOINT_BACKUP_UNAVAILABLE: Final[str] = (
    "memory.checkpoint.backup_unavailable"
)
MEMORY_CHECKPOINT_REREAD_FAILED: Final[str] = "memory.checkpoint.reread_failed"
MEMORY_CHECKPOINT_ROLLBACK: Final[str] = "memory.checkpoint.rollback"
MEMORY_CHECKPOINT_ROLLBACK_FAILED: Final[str] = "memory.checkpoint.rollback_failed"
# Emitted when an inner rollback step fails during a deploy / rollback
# recovery path (distinct from the overall rollback_failed event so
# alerting can detect partial-rollback conditions where the primary
# operation already has its own failure signal).
MEMORY_CHECKPOINT_ROLLBACK_STEP_FAILED: Final[str] = (
    "memory.checkpoint.rollback_step_failed"
)

# ── Composite routing ────────────────────────────────────────────

MEMORY_COMPOSITE_ROUTED: Final[str] = "memory.composite.routed"
MEMORY_COMPOSITE_FANOUT_START: Final[str] = "memory.composite.fanout_start"
MEMORY_COMPOSITE_FANOUT_COMPLETE: Final[str] = "memory.composite.fanout_complete"
MEMORY_COMPOSITE_FANOUT_PARTIAL: Final[str] = "memory.composite.fanout_partial"
MEMORY_COMPOSITE_ID_RESOLVED: Final[str] = "memory.composite.id_resolved"

# ── Sparse search ─────────────────────────────────────────────────

MEMORY_SPARSE_FIELD_ENSURED: Final[str] = "memory.sparse.field_ensured"
MEMORY_SPARSE_FIELD_ENSURE_FAILED: Final[str] = "memory.sparse.field_ensure_failed"
MEMORY_SPARSE_UPSERT_COMPLETE: Final[str] = "memory.sparse.upsert_complete"
MEMORY_SPARSE_UPSERT_FAILED: Final[str] = "memory.sparse.upsert_failed"
MEMORY_SPARSE_SEARCH_COMPLETE: Final[str] = "memory.sparse.search_complete"
MEMORY_SPARSE_SEARCH_FAILED: Final[str] = "memory.sparse.search_failed"
MEMORY_SPARSE_POINT_FIELD_DEFAULTED: Final[str] = "memory.sparse.point_field_defaulted"
MEMORY_SPARSE_BATCH_DEGRADED: Final[str] = "memory.sparse.batch_degraded"

# ── Query reformulation ───────────────────────────────────────────

MEMORY_REFORMULATION_FAILED: Final[str] = "memory.reformulation.failed"
MEMORY_SUFFICIENCY_CHECK_FAILED: Final[str] = "memory.sufficiency_check.failed"
MEMORY_REFORMULATION_ROUND: Final[str] = "memory.reformulation.round"
MEMORY_REFORMULATION_SUFFICIENT: Final[str] = "memory.reformulation.sufficient"
MEMORY_REFORMULATION_EXHAUSTED: Final[str] = "memory.reformulation.exhausted"
MEMORY_REFORMULATION_FINAL_CHECK: Final[str] = "memory.reformulation.final_check"

# ── Diversity re-ranking ─────────────────────────────────────────

MEMORY_DIVERSITY_RERANKED: Final[str] = "memory.ranking.diversity_reranked"
MEMORY_DIVERSITY_RERANK_FAILED: Final[str] = "memory.ranking.diversity_rerank_failed"

# ── Self-editing memory ───────────────────────────────────────────

MEMORY_SELF_EDIT_TOOL_EXECUTE: Final[str] = "memory.self_edit.tool.execute"
MEMORY_SELF_EDIT_CORE_READ: Final[str] = "memory.self_edit.core.read"
MEMORY_SELF_EDIT_CORE_WRITE: Final[str] = "memory.self_edit.core.write"
MEMORY_SELF_EDIT_CORE_WRITE_REJECTED: Final[str] = (
    "memory.self_edit.core.write_rejected"
)
MEMORY_SELF_EDIT_ARCHIVAL_SEARCH: Final[str] = "memory.self_edit.archival.search"
MEMORY_SELF_EDIT_ARCHIVAL_WRITE: Final[str] = "memory.self_edit.archival.write"
MEMORY_SELF_EDIT_RECALL_READ: Final[str] = "memory.self_edit.recall.read"
MEMORY_SELF_EDIT_RECALL_WRITE: Final[str] = "memory.self_edit.recall.write"
MEMORY_SELF_EDIT_WRITE_FAILED: Final[str] = "memory.self_edit.write.failed"
# Generic dispatch failure event covering any of the six self-editing
# tools (read, write, search, ...).  Logged at the catch-all dispatch
# boundary so a failed core_memory_read isn't mislabeled as a write.
MEMORY_SELF_EDIT_TOOL_FAILED: Final[str] = "memory.self_edit.tool.failed"

# ── Hierarchical retrieval ──────────────────────────────────────

MEMORY_HIERARCHICAL_ROUTING: Final[str] = "memory.hierarchical.routing"
MEMORY_HIERARCHICAL_WORKER_START: Final[str] = "memory.hierarchical.worker_start"
MEMORY_HIERARCHICAL_WORKER_COMPLETE: Final[str] = "memory.hierarchical.worker_complete"
MEMORY_HIERARCHICAL_WORKER_FAILED: Final[str] = "memory.hierarchical.worker_failed"
MEMORY_HIERARCHICAL_WORKER_DEGRADED: Final[str] = "memory.hierarchical.worker_degraded"
MEMORY_HIERARCHICAL_MERGE: Final[str] = "memory.hierarchical.merge"
MEMORY_HIERARCHICAL_RETRY: Final[str] = "memory.hierarchical.retry"
MEMORY_HIERARCHICAL_COMPLETE: Final[str] = "memory.hierarchical.complete"

# ── Query-specific re-ranking ──────────────────────────────────

MEMORY_RERANK_CACHE_HIT: Final[str] = "memory.rerank.cache_hit"
MEMORY_RERANK_CACHE_MISS: Final[str] = "memory.rerank.cache_miss"
MEMORY_RERANK_COMPLETE: Final[str] = "memory.rerank.complete"
MEMORY_RERANK_FAILED: Final[str] = "memory.rerank.failed"

# ── Knowledge Architect ─────────────────────────────────────────

KNOWLEDGE_ARCHITECT_WRITE: Final[str] = "memory.architect.write"
KNOWLEDGE_ARCHITECT_DELETE: Final[str] = "memory.architect.delete"
KNOWLEDGE_ARCHITECT_APPROVAL_CREATED: Final[str] = "memory.architect.approval_created"
KNOWLEDGE_ARCHITECT_WRITE_DENIED: Final[str] = "memory.architect.write_denied"
# Failure events for the org-memory tool wrappers.  Logged at WARNING
# before returning is_error=True so observability captures every
# failure, not just successful operations.
KNOWLEDGE_ARCHITECT_SEARCH_FAILED: Final[str] = "memory.architect.search.failed"
KNOWLEDGE_ARCHITECT_READ_FAILED: Final[str] = "memory.architect.read.failed"
KNOWLEDGE_ARCHITECT_WRITE_FAILED: Final[str] = "memory.architect.write.failed"
KNOWLEDGE_ARCHITECT_BROWSE_WIKI_FAILED: Final[str] = (
    "memory.architect.browse_wiki.failed"
)
