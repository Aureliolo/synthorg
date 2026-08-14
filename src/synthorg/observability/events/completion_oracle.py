# module-kind: declarative
"""Observability event constants for the build/test/review completion oracle.

Naming pattern follows the existing ``red_team`` and ``review_pipeline``
modules: ``<domain>.<noun>.<verb>``. Two families: ``completion_oracle.build_test.*``
for the deterministic execution-grounded gate and ``completion_oracle.review.*``
for the agent-session peer reviewer.
"""

from typing import Final

# ── Layer 1: execution-grounded build/test gate ────────────────────

BUILD_TEST_GATE_EVALUATED: Final[str] = "completion_oracle.build_test.evaluated"
"""Build/test oracle produced a verdict for a completing task."""

BUILD_TEST_GATE_BLOCKED: Final[str] = "completion_oracle.build_test.blocked"
"""Build/test verdict blocks completion (failing or unverified code task)."""

BUILD_TEST_CHECKER_UNAVAILABLE: Final[str] = (
    "completion_oracle.build_test.checker_unavailable"
)
"""Execution-record repository is unwired; the gate passes through (non-prod boot)."""

BUILD_TEST_CHECKER_FAULT: Final[str] = "completion_oracle.build_test.checker_fault"
"""Execution-record query raised; a REQUIRED code task fails closed to UNVERIFIED."""

# ── Layer 2: agent-session peer reviewer ───────────────────────────

COMPLETION_ORACLE_GATE_STARTED: Final[str] = "completion_oracle.review.gate_started"
"""Peer-review gate began evaluating a deliverable."""

COMPLETION_ORACLE_GATE_SKIPPED: Final[str] = "completion_oracle.review.gate_skipped"
"""Peer-review gate skipped (feature off, below stakes threshold, or shadow-only)."""

COMPLETION_ORACLE_CONFIG_RESOLVE_FAILED: Final[str] = (
    "completion_oracle.runtime.config_resolve_failed"
)
"""Settings resolution failed; the oracle falls back to its on-by-default config
(the gate stays ENABLED -- distinct from a deliberate skip)."""

COMPLETION_ORACLE_GATES_WIRED: Final[str] = "completion_oracle.runtime.gates_wired"
"""Oracle gates attached to (or, when disabled, detached from) the review gate,
at boot or on a hot-reload."""

COMPLETION_ORACLE_VERDICT_DUPLICATE: Final[str] = (
    "completion_oracle.review.verdict_duplicate"
)
"""A second verdict was submitted for an execution that already has one; the
duplicate is rejected."""

COMPLETION_ORACLE_GATE_BUILD_FAILED: Final[str] = (
    "completion_oracle.review.gate_build_failed"
)
"""Gate enabled but a boot-time build precondition failed (raises)."""

COMPLETION_ORACLE_AGENT_INVOKED: Final[str] = "completion_oracle.review.agent_invoked"
"""Gate dispatched the peer-review agent for inline evaluation."""

COMPLETION_ORACLE_AGENT_FAILED: Final[str] = "completion_oracle.review.agent_failed"
"""Peer-review agent dispatch or run raised; gate fails CLOSED to escalation."""

COMPLETION_ORACLE_VERDICT_RECEIVED: Final[str] = (
    "completion_oracle.review.verdict_received"
)
"""Reviewer filed a verdict via the ``submit_completion_oracle_verdict`` tool."""

COMPLETION_ORACLE_VERDICT_MISSING: Final[str] = (
    "completion_oracle.review.verdict_missing"
)
"""Reviewer did not file a verdict; gate escalates to human decision."""

COMPLETION_ORACLE_VERDICT_MISMATCH: Final[str] = (
    "completion_oracle.review.verdict_mismatch"
)
"""Stored verdict's execution_id/task_id differ from the gate input; escalate."""

COMPLETION_ORACLE_VERDICT_VALIDATION_FAILED: Final[str] = (
    "completion_oracle.review.verdict_validation_failed"
)
"""submit_completion_oracle_verdict payload failed schema validation."""

COMPLETION_ORACLE_GATE_APPROVED: Final[str] = "completion_oracle.review.gate_approved"
"""Reviewer approved; deliverable proceeds toward COMPLETED."""

COMPLETION_ORACLE_GATE_REJECTED: Final[str] = "completion_oracle.review.gate_rejected"
"""Reviewer rejected; deliverable routed back to IN_PROGRESS as rework."""

COMPLETION_ORACLE_GATE_ESCALATED: Final[str] = "completion_oracle.review.gate_escalated"
"""Reviewer escalated, or no distinct reviewer resolvable; parked for a human."""

COMPLETION_ORACLE_SHADOW_OBSERVED: Final[str] = (
    "completion_oracle.review.shadow_observed"
)
"""Shadow mode: verdict computed and surfaced but not enforced."""

COMPLETION_ORACLE_REWORK_ROUTED: Final[str] = "completion_oracle.review.rework_routed"
"""Review gate consumed a REJECT verdict and routed the task to IN_PROGRESS."""

COMPLETION_ORACLE_ESCALATION_ROUTED: Final[str] = (
    "completion_oracle.review.escalation_routed"
)
"""Review gate consumed an ESCALATE verdict and parked the task at BLOCKED for a
human decision, distinct from a REJECT's agent-rework routing."""

COMPLETION_ORACLE_NO_DISTINCT_REVIEWER: Final[str] = (
    "completion_oracle.review.no_distinct_reviewer"
)
"""No reviewer identity distinct from the executor could be resolved."""

COMPLETION_ORACLE_REVIEWER_UNSTAFFED: Final[str] = (
    "completion_oracle.review.reviewer_unstaffed"
)
"""No roster agent holds the Completion Reviewer role, so no independent
reviewer could be asked. Distinct from ``no_distinct_reviewer``, which means a
holder exists but is the executor: this one is answered by staffing the role."""

COMPLETION_ORACLE_PROJECT_READ_FAILED: Final[str] = (
    "completion_oracle.review.project_read_failed"
)
"""The reviewed work's project could not be read, so reviewer selection widened
org-wide instead of preferring a holder already on its team."""

COMPLETION_ORACLE_REPORT_ARCHIVED: Final[str] = (
    "completion_oracle.review.report_archived"
)
"""Verdict record persisted to the durable cross-process archive."""

COMPLETION_ORACLE_REPORT_ARCHIVE_FAILED: Final[str] = (
    "completion_oracle.review.report_archive_failed"
)
"""Durable archive write failed; the gate verdict stands (fail-OPEN audit)."""

COMPLETION_ORACLE_REPORT_SAVE_FAILED: Final[str] = (
    "completion_oracle.review.report_save_failed"
)
"""Archive repository failed to persist a verdict record."""

COMPLETION_ORACLE_REPORT_QUERY_FAILED: Final[str] = (
    "completion_oracle.review.report_query_failed"
)
"""Archive repository failed to read verdict records."""

COMPLETION_ORACLE_REPORT_DELETE_FAILED: Final[str] = (
    "completion_oracle.review.report_delete_failed"
)
"""Archive repository failed to purge verdict records before a threshold."""

COMPLETION_ORACLE_REPORT_DESERIALIZE_FAILED: Final[str] = (
    "completion_oracle.review.report_deserialize_failed"
)
"""A stored verdict row could not be decoded back into a record."""

COMPLETION_ORACLE_REPORTS_LISTED: Final[str] = "completion_oracle.review.reports_listed"
"""An operator read a page of archived verdicts."""
