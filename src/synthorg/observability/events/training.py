"""Training mode event constants for structured logging.

Constants follow the ``hr.training.<action>`` naming convention
and are passed as the first argument to structured log calls.
"""

from typing import Final

# -- Plan lifecycle ---------------------------------------------------

HR_TRAINING_PLAN_CREATED: Final[str] = "hr.training.plan_created"
HR_TRAINING_PLAN_EXECUTED: Final[str] = "hr.training.plan_executed"
HR_TRAINING_PLAN_IDEMPOTENT: Final[str] = "hr.training.plan_idempotent"
HR_TRAINING_PLAN_OVERRIDES_UPDATED: Final[str] = "hr.training.plan_overrides_updated"
HR_TRAINING_PLAN_FAILED: Final[str] = "hr.training.plan_failed"
HR_TRAINING_SKIPPED: Final[str] = "hr.training.skipped"

HR_TRAINING_PLAN_STATUS_TRANSITIONED: Final[str] = (
    "hr.training.plan.status_transitioned"
)
"""Training plan status transition (any persisted hop).

Emitted AFTER the in-memory session-store write succeeds, carrying
``plan_id`` / ``from_status`` / ``to_status``. Mirrors
``HR_AGENT_STATUS_TRANSITIONED`` (`synthorg.observability.events.hr`,
emitted on every agent registry status hop) and
``WORKFLOW_EXEC_STATUS_TRANSITIONED`` /
``WORKFLOW_EXEC_NODE_STATUS_TRANSITIONED``
(`synthorg.observability.events.workflow_execution`, emitted on
every workflow/sprint execution hop): every persisted hop is logged
so the audit stream sees the actual state change."""

# -- Source selection -------------------------------------------------

HR_TRAINING_SELECTION_STARTED: Final[str] = "hr.training.selection_started"
HR_TRAINING_SELECTION_COMPLETE: Final[str] = "hr.training.selection_complete"
HR_TRAINING_SELECTION_SKIPPED: Final[str] = "hr.training.selection_skipped"
HR_TRAINING_AGENT_NOT_FOUND: Final[str] = "hr.training.agent_not_found"

# -- Extraction -------------------------------------------------------

HR_TRAINING_EXTRACTION_STARTED: Final[str] = "hr.training.extraction_started"
HR_TRAINING_EXTRACTION_FAILED: Final[str] = "hr.training.extraction_failed"
HR_TRAINING_ITEMS_EXTRACTED: Final[str] = "hr.training.items_extracted"

# -- Curation ---------------------------------------------------------

HR_TRAINING_CURATION_COMPLETE: Final[str] = "hr.training.curation_complete"
HR_TRAINING_CURATION_FALLBACK: Final[str] = "hr.training.curation_fallback"
HR_TRAINING_CURATION_FAILED: Final[str] = "hr.training.curation_failed"

# -- Guards -----------------------------------------------------------

HR_TRAINING_GUARD_EVALUATION: Final[str] = "hr.training.guard_evaluation"
HR_TRAINING_GUARD_FAILED: Final[str] = "hr.training.guard_failed"
HR_TRAINING_SANITIZATION_APPLIED: Final[str] = "hr.training.sanitization_applied"
HR_TRAINING_VOLUME_CAP_ENFORCED: Final[str] = "hr.training.volume_cap_enforced"
HR_TRAINING_REVIEW_GATE_CREATED: Final[str] = "hr.training.review_gate_created"
HR_TRAINING_REVIEW_GATE_FAILED: Final[str] = "hr.training.review_gate_failed"
HR_TRAINING_REVIEW_PENDING: Final[str] = "hr.training.review_pending"

# -- Error paths ------------------------------------------------------

HR_TRAINING_STORE_FAILED: Final[str] = "hr.training.store_failed"
HR_TRAINING_SELECTOR_CONFIG_INVALID: Final[str] = "hr.training.selector_config_invalid"
HR_TRAINING_EXTRACTOR_CONFIG_INVALID: Final[str] = (
    "hr.training.extractor_config_invalid"
)
HR_TRAINING_CONFIG_INVALID: Final[str] = "hr.training.config_invalid"

# -- Persistence --------------------------------------------------

HR_TRAINING_PLAN_PERSISTED: Final[str] = "hr.training.plan_persisted"
HR_TRAINING_RESULT_PERSISTED: Final[str] = "hr.training.result_persisted"
HR_TRAINING_PERSISTENCE_ERROR: Final[str] = "hr.training.persistence_error"
