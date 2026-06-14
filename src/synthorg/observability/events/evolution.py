"""Evolution system event constants for structured logging.

Constants follow the ``evolution.<subject>.<action>`` naming convention
and are passed as the first argument to structured log calls.
"""

from typing import Final

# ── Trigger events ───────────────────────────────────────────────

EVOLUTION_TRIGGER_REQUESTED: Final[str] = "evolution.trigger.requested"
EVOLUTION_TRIGGER_SKIPPED: Final[str] = "evolution.trigger.skipped"
EVOLUTION_TRIGGER_FAILED: Final[str] = "evolution.trigger.failed"
EVOLUTION_TRIGGER_RUN_RECORDED: Final[str] = "evolution.trigger.run_recorded"

# ── Proposal events ─────────────────────────────────────────────

EVOLUTION_PROPOSAL_GENERATED: Final[str] = "evolution.proposal.generated"
EVOLUTION_PROPOSAL_REJECTED: Final[str] = "evolution.proposal.rejected"

# ── Proposer events ─────────────────────────────────────────────

EVOLUTION_PROPOSER_INIT: Final[str] = "evolution.proposer.init"
EVOLUTION_PROPOSER_ANALYZE: Final[str] = "evolution.proposer.analyze"
EVOLUTION_PROPOSER_PARSE_ERROR: Final[str] = "evolution.proposer.parse_error"
EVOLUTION_PROPOSER_ROUTE: Final[str] = "evolution.proposer.route"

# ── Guard events ─────────────────────────────────────────────────

EVOLUTION_GUARDS_PASSED: Final[str] = "evolution.guards.passed"
EVOLUTION_GUARDS_REJECTED: Final[str] = "evolution.guards.rejected"
EVOLUTION_REVIEW_GATE_APPROVED: Final[str] = "evolution.review_gate.approved"
EVOLUTION_REVIEW_GATE_REJECTED: Final[str] = "evolution.review_gate.rejected"

# ── Adaptation events ───────────────────────────────────────────

EVOLUTION_ADAPTED: Final[str] = "evolution.adaptation.applied"
EVOLUTION_ADAPTATION_FAILED: Final[str] = "evolution.adaptation.failed"

# ── Rollback events ─────────────────────────────────────────────

EVOLUTION_ROLLBACK_TRIGGERED: Final[str] = "evolution.rollback.triggered"
EVOLUTION_ROLLBACK_FAILED: Final[str] = "evolution.rollback.failed"

# ── Rate limiting ───────────────────────────────────────────────

EVOLUTION_RATE_LIMITED: Final[str] = "evolution.rate_limit.exceeded"

# ── Service-level events ────────────────────────────────────────

EVOLUTION_SERVICE_STARTED: Final[str] = "evolution.service.started"
EVOLUTION_SERVICE_COMPLETE: Final[str] = "evolution.service.complete"
EVOLUTION_CONTEXT_BUILD_FAILED: Final[str] = "evolution.context.build_failed"
EVOLUTION_CONTEXT_SNAPSHOT_FAILED: Final[str] = "evolution.context.snapshot_failed"
EVOLUTION_CONTEXT_MEMORY_FAILED: Final[str] = "evolution.context.memory_failed"

# ── Shadow evaluation events ───────────────────────────────────

EVOLUTION_SHADOW_STARTED: Final[str] = "evolution.shadow.started"
EVOLUTION_SHADOW_COMPLETED: Final[str] = "evolution.shadow.completed"
EVOLUTION_SHADOW_REGRESSION: Final[str] = "evolution.shadow.regression"
EVOLUTION_SHADOW_TASK_FAILED: Final[str] = "evolution.shadow.task_failed"
EVOLUTION_SHADOW_EMPTY_SUITE: Final[str] = "evolution.shadow.empty_suite"
EVOLUTION_SHADOW_INCONCLUSIVE: Final[str] = "evolution.shadow.inconclusive"
EVOLUTION_SHADOW_MISCONFIGURED: Final[str] = "evolution.shadow.misconfigured"

# ── Factory/config errors ──────────────────────────────────────

EVOLUTION_INVALID_STORE_TYPE: Final[str] = "evolution.store.invalid_type"

# ── Outcome store events (feed the SignalsService evolution aggregator)

EVOLUTION_OUTCOME_RECORDED: Final[str] = "evolution.outcome.recorded"
EVOLUTION_OUTCOME_RECORD_FAILED: Final[str] = "evolution.outcome.record_failed"
EVOLUTION_OUTCOME_STORE_EVICTED: Final[str] = "evolution.outcome.store_evicted"

EVOLUTION_GUARD_DECISION: Final[str] = "evolution.guard.decision"
"""Composite guard reached a decision.

Carries the guard chain result (approve / reject / request-review)
plus the per-guard breakdown so operators can audit which guard
gated a proposal."""

EVOLUTION_GUARD_INVALID_CONFIG: Final[str] = "evolution.guard.invalid_config"
"""Guard rejected operator-supplied construction parameters.

Emitted before the guard ``__init__`` raises ``ValueError`` so
operators see the field, value, and constraint that failed without
having to wait for the traceback.  Typical fields: ``field``,
``value``, ``constraint``."""
