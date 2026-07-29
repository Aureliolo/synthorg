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
MEMORY_BACKEND_WIRED: Final[str] = "memory.backend.wired"
"""Emitted at INFO once the boot path publishes a usable backend."""

MEMORY_BACKEND_WIRE_SKIPPED: Final[str] = "memory.backend.wire_skipped"
"""Emitted at WARNING when boot deliberately wires no memory backend.

Distinct from a construction failure: this is the expected shape when a
prerequisite (persistence, an embedder) is absent, and it is the signal
an operator alerts on to learn that agent memory is off.
"""

MEMORY_BACKEND_WIRE_FAILED: Final[str] = "memory.backend.wire_failed"
"""Emitted at ERROR when the backend could not be built or connected."""
MEMORY_BACKEND_SYSTEM_ERROR: Final[str] = "memory.backend.system_error"

# ── Entry operations ──────────────────────────────────────────────

MEMORY_ENTRY_STORED: Final[str] = "memory.entry.stored"
MEMORY_ENTRY_STORE_FAILED: Final[str] = "memory.entry.store_failed"
MEMORY_ENTRY_RETRIEVED: Final[str] = "memory.entry.retrieved"
MEMORY_ENTRY_RETRIEVAL_FAILED: Final[str] = "memory.entry.retrieval_failed"
MEMORY_ENTRY_DELETED: Final[str] = "memory.entry.deleted"
MEMORY_ENTRY_DELETE_FAILED: Final[str] = "memory.entry.delete_failed"
MEMORY_ENTRY_UPDATED: Final[str] = "memory.entry.updated"
MEMORY_ENTRY_UPDATE_FAILED: Final[str] = "memory.entry.update_failed"
MEMORY_ENTRY_FETCHED: Final[str] = "memory.entry.fetched"
MEMORY_ENTRY_FETCH_FAILED: Final[str] = "memory.entry.fetch_failed"
MEMORY_ENTRY_COUNTED: Final[str] = "memory.entry.counted"
MEMORY_ENTRY_COUNT_FAILED: Final[str] = "memory.entry.count_failed"

MEMORY_EMBEDDING_FAILED: Final[str] = "memory.embedding.failed"
"""Emitted at WARNING when an embedding call fails for a batch."""

MEMORY_EMBEDDING_RETRIED: Final[str] = "memory.embedding.retried"
"""Emitted when a transient embedding failure is retried with backoff."""

MEMORY_EMBEDDING_TRUNCATED: Final[str] = "memory.embedding.truncated"
"""Emitted at DEBUG when a vector is narrowed to the configured width.

The Matryoshka tail is dropped and the head renormalised, which changes
the stored vector without changing the recall it supports. DEBUG because
it is the operator's own pinned width being honoured, not a fault, but
recorded so an unexpected width is traceable to the call that narrowed it.
"""

MEMORY_EMBEDDING_COST_RECORD_FAILED: Final[str] = "memory.embedding.cost_record_failed"
"""Emitted at WARNING when a batch's spend could not be attributed.

The embedding itself succeeded; what is lost is the accounting, so the
call continues and the gap is reported rather than costing recall.
"""

MEMORY_DENSE_INDEX_READY: Final[str] = "memory.dense_index.ready"
"""Emitted at INFO once the dense vector index is loaded and usable."""

MEMORY_DENSE_INDEX_UNAVAILABLE: Final[str] = "memory.dense_index.unavailable"
"""Emitted at WARNING when the dense vector index cannot be prepared.

Semantic recall is impossible in this state. The repository degrades
rather than raising so persistence stays up for every non-memory
feature sharing the connection; the memory backend is what turns this
into a loud failure at its own boundary.
"""
MEMORY_DENSE_COLUMN_STALE: Final[str] = "memory.dense_column.stale"
"""Emitted at INFO for an empty dense column left by an earlier width.

It strands no vectors, so it is not a fault; it is schema drift that no
other report covers, because the orphaned-width error only fires when a
leftover column still holds rows.
"""

MEMORY_DENSE_INDEX_INVALID: Final[str] = "memory.dense_index.invalid"
"""Emitted at WARNING when a crash-left invalid index is found.

``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` matches on name alone, so an
index abandoned mid-build would otherwise be accepted as present and
never rebuilt, leaving every dense query on a sequential scan. The event
marks the start of the drop-and-recreate, not its success: either step
can still fail, and the readiness path reports that separately. Recorded
rather than fixed silently, because repeated occurrences mean builds
keep dying.
"""

MEMORY_DENSE_INDEX_UNINDEXABLE: Final[str] = "memory.dense_index.unindexable"
"""Emitted at ERROR when the width is too wide for any ANN index.

Distinct from :data:`MEMORY_DENSE_INDEX_UNAVAILABLE`: dense recall still
answers here, by scanning the whole corpus for every query. Kept rather
than refused because exact search beats no semantic recall, but reported
loudly and surfaced as DEGRADED, since the cost grows with the corpus and
nothing else about the system looks wrong.
"""

MEMORY_DENSE_SESSION_DISCARDED: Final[str] = "memory.dense_index.session_discarded"
"""Emitted at ERROR when a build connection could not be restored.

The build runs on a pooled connection, so a failed cleanup is not a
private problem: the next checkout would inherit a held advisory lock
(blocking every later builder) or a ``statement_timeout`` it never asked
for. The connection is closed rather than returned, which costs one
reconnect and bounds the damage to this checkout.
"""

MEMORY_DENSE_INDEX_BUILD_CONTENDED: Final[str] = "memory.dense_index.build_contended"
"""Emitted at WARNING when the build lock was still held at the deadline.

A sibling process is building the same width, which on an established
corpus can outlast any wait worth blocking a boot for. Distinct from
:data:`MEMORY_DENSE_INDEX_UNAVAILABLE`, which describes a state nothing
will fix on its own: this one resolves itself when the other builder
finishes, and the next readiness call picks the column up.
"""

MEMORY_DENSE_INDEX_PERMISSION_DENIED: Final[str] = (
    "memory.dense_index.permission_denied"
)
"""Emitted at ERROR when the role may not install the vector extension.

pgvector is not a trusted extension, so ``CREATE EXTENSION`` needs
superuser. A deployment whose application role is correctly
least-privileged therefore gets lexical-only recall while CI and the
bundled image, which connect as superuser, get semantic recall: the
divergence an operator is least likely to anticipate, so it is reported
as its own condition rather than as a generic unavailable index.
"""

MEMORY_DENSE_INDEX_WIDTH_CHANGED: Final[str] = "memory.dense_index.width_changed"
"""Emitted at ERROR when a dense index survives from a different width.

Embeddings from different models are not comparable, so the index is
keyed by width. Vectors written under a previous width therefore become
unreachable the moment the embedding model changes: recall silently
returns nothing rather than returning something wrong. Reported loudly
because an operator who is not told will read empty recall as a bug in
memory rather than as the model swap they just performed.
"""

MEMORY_DENSE_INDEX_SCAN_FAILED: Final[str] = "memory.dense_index.scan_failed"
"""Emitted at WARNING when the orphaned-width scan cannot complete.

The dense index is usable either way; what is lost is the ability to
say whether vectors from a previous embedding width are stranded. Worth
reporting rather than swallowing, because absence of a width-change
error would otherwise read as proof that none occurred.
"""

MEMORY_WRITE_GATE_DECIDED: Final[str] = "memory.write_gate.decided"
"""Emitted at INFO for every gated memory write.

Carries the disposition plus the duplicate / superseded ids, so a write
that was deliberately dropped is distinguishable from one that never
happened.
"""

MEMORY_WRITE_GATE_DEGRADED: Final[str] = "memory.write_gate.degraded"
"""Emitted at WARNING when the gate cannot read comparable entries.

The write still proceeds: failing open risks storing a duplicate, while
failing closed would discard a real memory, which is the worse loss. The
event records that deduplication was skipped for this write.
"""

MEMORY_TOPIC_SCOPE_APPLIED: Final[str] = "memory.topic_scope.applied"
"""Emitted at INFO when topic scoping drops off-topic procedural lessons.

Carries the candidate and retained counts so an operator can see that
abstention was deliberate. Without it, a lesson correctly withheld from
an unrelated task is indistinguishable from memory failing to recall.
"""

MEMORY_RRF_PIPELINE_COMPLETED: Final[str] = "memory.rrf.pipeline_completed"
"""Emitted at DEBUG after the RRF hybrid pipeline fuses + filters results.

Carries dense / sparse / fused counts so retrieval behaviour is
observable without a debugger (an unexpectedly empty fused result is
then traceable to a threshold or an empty arm)."""

# ── Shared knowledge ─────────────────────────────────────────────

MEMORY_SHARED_PUBLISHED: Final[str] = "memory.shared.published"
MEMORY_SHARED_PUBLISH_FAILED: Final[str] = "memory.shared.publish_failed"
MEMORY_SHARED_SEARCHED: Final[str] = "memory.shared.searched"
MEMORY_SHARED_SEARCH_FAILED: Final[str] = "memory.shared.search_failed"
MEMORY_SHARED_RETRACTED: Final[str] = "memory.shared.retracted"
MEMORY_SHARED_RETRACT_FAILED: Final[str] = "memory.shared.retract_failed"

# ── Validation ──────────────────────────────────────────────────

MEMORY_MODEL_INVALID: Final[str] = "memory.model.invalid"

MEMORY_CONTENT_REDACTED: Final[str] = "memory.content.redacted"
"""Emitted at WARNING when candidate memory text carried a secret.

Carries the finding names only, never the matched text. Worth WARNING
rather than INFO: memory is re-injected into later prompts, so a
credential reaching this point means one leaked into a tool result or an
agent's own write and the source is worth chasing.
"""

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
# Engine context-injection dispatch: emitted when the wired injection strategy
# surfaces memories into an agent's pre-execution context, or when that call
# fails unexpectedly (non-fatal -- the run proceeds without injected memory).
MEMORY_CONTEXT_INJECTED: Final[str] = "memory.context.injected"
MEMORY_CONTEXT_INJECTION_FAILED: Final[str] = "memory.context.injection_failed"

# ── Memory filter ──────────────────────────────────────────────

MEMORY_FILTER_INIT: Final[str] = "memory.filter.init"
MEMORY_FILTER_APPLIED: Final[str] = "memory.filter.applied"
MEMORY_FILTER_STORE_MISSING_TAG: Final[str] = "memory.filter.store_missing_tag"

# ── Embedding selection ──────────────────────────────────────────

MEMORY_EMBEDDER_RESOLVED: Final[str] = "memory.embedder.resolved"
"""Emitted at INFO with the provider, model and width boot settled on."""

MEMORY_EMBEDDER_UNRESOLVED: Final[str] = "memory.embedder.unresolved"
"""Emitted at ERROR when no embedding model could be resolved at boot.

Semantic memory cannot start without one, so this is the root cause an
operator needs when the dashboard reports memory off.
"""

MEMORY_EMBEDDER_PROBED: Final[str] = "memory.embedder.probed"
"""Emitted at INFO with a model's measured width.

The width is the model's own answer rather than a catalogued figure, so
this event is the record of what the vector column was built for.
"""

MEMORY_EMBEDDER_PROBE_FAILED: Final[str] = "memory.embedder.probe_failed"
"""Emitted at WARNING when a model could not answer a width probe.

Separate from the success event so an alert on a failing probe does not
also fire on every successful one, and carries ``reason`` distinguishing
an unreachable model, a refused request, a deadline, and a response that
arrived carrying no vector.
"""

MEMORY_EMBEDDER_WIDTH_REJECTED: Final[str] = "memory.embedder.width_rejected"
"""Emitted at WARNING when a measured width exceeds what the store holds.

Distinct from a width the store can hold but not index, which is a
degradation rather than a refusal and is reported by the health surface.
"""

MEMORY_EMBEDDER_BUILTIN_SELECTED: Final[str] = "memory.embedder.builtin_selected"
"""Emitted at WARNING when the operator chooses the built-in embedder.

Recall becomes lexical, so the choice is recorded even though it was
deliberate: the log is where an operator debugging poor recall months
later will look, and by then the reason will not be obvious.
"""

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
MEMORY_FINE_TUNE_WIRING_FAILED: Final[str] = "memory.fine_tune.wiring_failed"
MEMORY_FINE_TUNE_INVALID_REQUEST: Final[str] = "memory.fine_tune.invalid_request"
MEMORY_FINE_TUNE_RESUME_REJECTED: Final[str] = "memory.fine_tune.resume_rejected"
MEMORY_FINE_TUNE_INTERRUPTED: Final[str] = "memory.fine_tune.interrupted"
MEMORY_FINE_TUNE_DEPENDENCY_MISSING: Final[str] = "memory.fine_tune.dependency_missing"
MEMORY_FINE_TUNE_CHECKPOINT_SAVED: Final[str] = "memory.fine_tune.checkpoint_saved"
MEMORY_FINE_TUNE_CHECKPOINT_DEPLOYED: Final[str] = (
    "memory.fine_tune.checkpoint_deployed"
)
MEMORY_FINE_TUNE_CHECKPOINT_REJECTED: Final[str] = (
    "memory.fine_tune.checkpoint_rejected"
)
MEMORY_TRAINING_SOURCE_HARVESTED: Final[str] = "memory.training_source.harvested"
MEMORY_TRAINING_SOURCE_DEGRADED: Final[str] = "memory.training_source.degraded"
MEMORY_FINE_TUNE_CHECKPOINT_ROLLED_BACK: Final[str] = (
    "memory.fine_tune.checkpoint_rolled_back"
)
MEMORY_FINE_TUNE_CHECKPOINT_DELETED: Final[str] = "memory.fine_tune.checkpoint_deleted"
MEMORY_FINE_TUNE_PREFLIGHT_COMPLETED: Final[str] = (
    "memory.fine_tune.preflight_completed"
)
MEMORY_FINE_TUNE_THRESHOLD_FALLBACK: Final[str] = "memory.fine_tune.threshold_fallback"
MEMORY_FINE_TUNE_QUERY_LLM_FALLBACK: Final[str] = "memory.fine_tune.query_llm_fallback"
MEMORY_FINE_TUNE_QUERY_GENERATION_ERROR: Final[str] = (
    "memory.fine_tune.query_generation_error"
)
MEMORY_FINE_TUNE_PREFLIGHT_CHECK_DEGRADED: Final[str] = (
    "memory.fine_tune.preflight_check_degraded"
)
MEMORY_FINE_TUNE_PREFLIGHT_TIMED_OUT: Final[str] = (
    "memory.fine_tune.preflight_timed_out"
)
MEMORY_FINE_TUNE_EVAL_COMPLETED: Final[str] = "memory.fine_tune.eval_completed"
MEMORY_FINE_TUNE_EVAL_METRICS_UNREADABLE: Final[str] = (
    "memory.fine_tune.eval_metrics_unreadable"
)
MEMORY_FINE_TUNE_BACKUP_READ_SKIPPED: Final[str] = (
    "memory.fine_tune.backup_read_skipped"
)
MEMORY_FINE_TUNE_WS_EMIT_FAILED: Final[str] = "memory.fine_tune.ws_emit_failed"
MEMORY_FINE_TUNE_PERSIST_FAILED: Final[str] = "memory.fine_tune.persist_failed"
MEMORY_FINE_TUNE_ENCODE_INVOKED: Final[str] = "memory.fine_tune.encode_invoked"
MEMORY_FINE_TUNE_ENCODE_TRUNCATION_LIKELY: Final[str] = (
    "memory.fine_tune.encode_truncation_likely"
)
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
MEMORY_CHECKPOINT_DELETE_FAILED: Final[str] = "memory.checkpoint.delete_failed"

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
KNOWLEDGE_ARCHITECT_DELETE_FAILED: Final[str] = "memory.architect.delete.failed"
KNOWLEDGE_ARCHITECT_BROWSE_WIKI_FAILED: Final[str] = (
    "memory.architect.browse_wiki.failed"
)
