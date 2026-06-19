"""Middleware event constants.

Constants for structured logging of agent middleware, coordination
middleware, S1 constraint, and task-ledger constraint lifecycle events.
"""

# ── Agent middleware lifecycle ─────────────────────────────────────

MIDDLEWARE_CHAIN_BUILT: str = "middleware.agent_chain.built"
"""Agent middleware chain assembled from configuration."""

MIDDLEWARE_BEFORE_AGENT: str = "middleware.agent.before_agent"
"""Agent middleware ``before_agent`` hook invoked."""

MIDDLEWARE_AFTER_AGENT: str = "middleware.agent.after_agent"
"""Agent middleware ``after_agent`` hook invoked."""

MIDDLEWARE_BEFORE_MODEL: str = "middleware.agent.before_model"
"""Agent middleware ``before_model`` hook invoked."""

MIDDLEWARE_AFTER_MODEL: str = "middleware.agent.after_model"
"""Agent middleware ``after_model`` hook invoked."""

MIDDLEWARE_WRAP_MODEL_CALL: str = "middleware.agent.wrap_model_call"
"""Agent middleware ``wrap_model_call`` hook invoked."""

MIDDLEWARE_WRAP_TOOL_CALL: str = "middleware.agent.wrap_tool_call"
"""Agent middleware ``wrap_tool_call`` hook invoked."""

MIDDLEWARE_TOOL_CALL_INVALID: str = "middleware.tool_call.invalid"
"""A ToolCallRecord failed its success/error consistency invariant."""

MIDDLEWARE_HOOK_ERROR: str = "middleware.agent.hook_error"
"""An agent middleware hook raised an exception."""

MIDDLEWARE_SKIPPED: str = "middleware.agent.skipped"
"""An agent middleware was skipped (missing dependency)."""

# ── Coordination middleware lifecycle ──────────────────────────────

MIDDLEWARE_COORDINATION_CHAIN_BUILT: str = "middleware.coordination_chain.built"
"""Coordination middleware chain assembled from configuration."""

MIDDLEWARE_COORDINATION_HOOK_ERROR: str = "middleware.coordination.hook_error"
"""A coordination middleware hook raised an exception."""

MIDDLEWARE_COORDINATION_SKIPPED: str = "middleware.coordination.skipped"
"""A coordination middleware was skipped (missing dependency)."""

# ── S1 constraint events ──────────────────────────────────────────

MIDDLEWARE_AUTHORITY_DEFERENCE_DETECTED: str = "middleware.authority_deference.detected"
"""Authority cues detected in transcript by AuthorityDeferenceGuard."""

MIDDLEWARE_ASSUMPTION_VIOLATION_DETECTED: str = (
    "middleware.assumption_violation.detected"
)
"""Assumption violation detected in model response."""

MIDDLEWARE_CLARIFICATION_REQUIRED: str = "middleware.clarification_gate.required"
"""Pre-decomposition clarification gate rejected vague criteria."""

MIDDLEWARE_DELEGATION_HASH_RECORDED: str = "middleware.delegation_hash.recorded"
"""Delegation-chain content hash recorded for task."""

MIDDLEWARE_DELEGATION_HASH_DRIFT: str = "middleware.delegation_hash.drift"
"""Delegation-chain content hash drifted from root task."""

# ── Task-ledger constraint events ─────────────────────────────────

MIDDLEWARE_TASK_LEDGER_CREATED: str = "middleware.task_ledger.created"
"""TaskLedger created from decomposition plan."""

MIDDLEWARE_PROGRESS_LEDGER_EMITTED: str = "middleware.progress_ledger.emitted"
"""ProgressLedger emitted after rollup analysis."""

MIDDLEWARE_PLAN_REVIEW_GATED: str = "middleware.plan_review.gated"
"""Plan dispatch gated for approval review."""

MIDDLEWARE_PLAN_REVIEW_APPROVED: str = "middleware.plan_review.approved"
"""Plan dispatch approved after review."""

COORDINATION_REPLAN: str = "middleware.coordination.replan"
"""Coordination replan triggered after stall detection."""

COORDINATION_REPLAN_BUDGET_BLOCKED: str = (
    "middleware.coordination.replan_budget_blocked"
)
"""Replan blocked by budget affordability check."""

COORDINATION_REPLAN_CAP_REACHED: str = "middleware.coordination.replan_cap_reached"
"""Replan skipped because stall or reset cap was reached."""

# ── Registry events ──────────────────────────────────────────────

MIDDLEWARE_REGISTRATION_CONFLICT: str = "middleware.registry.conflict"
"""Conflicting middleware registration attempted."""

MIDDLEWARE_UNKNOWN: str = "middleware.registry.unknown"
"""Unknown middleware name requested from registry."""

MIDDLEWARE_DEFAULTS_REGISTERED: str = "middleware.defaults.registered"
"""Default middleware factories registered."""

MIDDLEWARE_DUPLICATE_CHAIN: str = "middleware.chain.duplicate"
"""Duplicate middleware names detected during chain construction."""

# ── Semantic drift detector events ──────────────────────────────

MIDDLEWARE_SEMANTIC_DRIFT_DETECTED: str = "middleware.semantic_drift.detected"
"""Semantic drift detected between model output and task criteria."""

MIDDLEWARE_SEMANTIC_DRIFT_SKIPPED: str = "middleware.semantic_drift.skipped"
"""Semantic drift check skipped (missing acceptance criteria)."""

MIDDLEWARE_SEMANTIC_DRIFT_ERROR: str = "middleware.semantic_drift.error"
"""Semantic drift computation failed (fail-soft)."""
