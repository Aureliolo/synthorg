"""Work pipeline event constants."""

from typing import Final

PIPELINE_RUN_STARTED: Final[str] = "pipeline.run.started"
PIPELINE_RUN_COMPLETED: Final[str] = "pipeline.run.completed"
PIPELINE_RUN_FAILED: Final[str] = "pipeline.run.failed"

PIPELINE_PHASE_STARTED: Final[str] = "pipeline.phase.started"
PIPELINE_PHASE_COMPLETED: Final[str] = "pipeline.phase.completed"
PIPELINE_PHASE_FAILED: Final[str] = "pipeline.phase.failed"

PIPELINE_ROUTING_DECIDED: Final[str] = "pipeline.routing.decided"
PIPELINE_SOLO_AGENT_SELECTED: Final[str] = "pipeline.solo.agent_selected"
PIPELINE_PROJECT_OWNER_SELECTED: Final[str] = "pipeline.project.owner_selected"
"""A single accountable owner was staffed onto a planned initiative."""

PIPELINE_PROJECT_LEAD_STAMPED: Final[str] = "pipeline.project.lead_stamped"
"""The staffed owner was persisted as the project's durable lead."""

PIPELINE_PROJECT_LEAD_ORPHANED: Final[str] = "pipeline.project.lead_orphaned"
"""A project's durable lead no longer resolves to a known agent."""

PIPELINE_PROJECT_LEAD_UNAVAILABLE: Final[str] = "pipeline.project.lead_unavailable"
"""A project's durable lead resolves but its bound pair cannot serve."""

PIPELINE_PROJECT_LEAD_CONTENDED: Final[str] = "pipeline.project.lead_contended"
"""A concurrent write won the lead-stamp race; re-reading the durable lead."""

PIPELINE_PROJECT_ROSTER_EMPTY: Final[str] = "pipeline.project.roster_empty"
"""No agents exist to staff as the initiative's owner."""

PIPELINE_WORK_INTAKE_REJECTED: Final[str] = "pipeline.work_intake.rejected"
"""Work intake rejected the request (or produced no usable task)."""

PIPELINE_PROJECT_NOT_FOUND: Final[str] = "pipeline.project.not_found"
"""The project referenced by the work item is absent."""

PIPELINE_ROUTING_UNDECIDABLE: Final[str] = "pipeline.routing.undecidable"
"""No viable agent could be selected for the routed work."""

PIPELINE_TEAM_PATH_UNAVAILABLE: Final[str] = "pipeline.team.unavailable"
"""Team coordination requested but no coordinator is wired."""

PIPELINE_TASK_MISSING: Final[str] = "pipeline.task.missing"
"""A task expected to exist was absent after a pipeline phase."""

PIPELINE_REFINEMENT_REQUESTED: Final[str] = "pipeline.refinement.requested"
"""Team-bound work lacked a definition of done; refinement was opened."""

PIPELINE_PLAN_SHELL_OPENED: Final[str] = "pipeline.plan_review.shell_opened"
"""A PLANNING plan shell was persisted at greenlight, before decomposition."""

PIPELINE_PLAN_REVIEW_REQUESTED: Final[str] = "pipeline.plan_review.requested"
"""A decomposed plan was parked for human approval before team dispatch."""

PIPELINE_PLAN_QUESTION_PARKED: Final[str] = "pipeline.plan_review.question_parked"
"""A plan's unresolved questions were parked as answerable questions."""

PIPELINE_PLAN_QUESTION_ANSWERED: Final[str] = "pipeline.plan_review.question_answered"
"""A decided plan question was written back onto the durable plan."""

PIPELINE_PLAN_QUESTION_WRITE_FAILED: Final[str] = (
    "pipeline.plan_review.question_write_failed"
)
"""A decided plan question could not be written back on the decision path."""

PIPELINE_PLAN_QUESTION_REPLAYED: Final[str] = "pipeline.plan_review.question_replayed"
"""Answers decided earlier were replayed onto the plan before it dispatched."""

PIPELINE_PLAN_QUESTION_RETIRED: Final[str] = "pipeline.plan_review.question_retired"
"""Unanswered plan questions were closed at dispatch; the build settled them."""

PIPELINE_PLAN_QUESTION_RETIRE_FAILED: Final[str] = (
    "pipeline.plan_review.question_retire_failed"
)
"""An unanswered plan question could not be closed at dispatch."""

PIPELINE_PLAN_DECISION_RESOLVED: Final[str] = "pipeline.plan_review.decision_resolved"
"""An approved decision item's option was recorded on the plan before dispatch."""

PIPELINE_PLAN_PARENT_MISSING: Final[str] = "pipeline.plan_review.parent_missing"
"""The objective task was deleted mid-decomposition; the plan is not parked."""

PIPELINE_PLAN_DECOMPOSITION_FAILED: Final[str] = (
    "pipeline.plan_review.decomposition_failed"
)
"""Decomposition (or plan parking) failed; the plan shell is being marked FAILED."""

PIPELINE_PLAN_MARKED_FAILED: Final[str] = "pipeline.plan_review.marked_failed"
"""A plan shell was successfully transitioned to FAILED, carrying its reason."""

PIPELINE_PLAN_FAIL_SHELL_MISSING: Final[str] = "pipeline.plan_review.fail_shell_missing"
"""fail_plan was asked to mark a plan FAILED but its shell was already gone."""

PIPELINE_PLAN_FAIL_WRITE_FAILED: Final[str] = "pipeline.plan_review.fail_write_failed"
"""The compensating write to mark a plan FAILED itself failed; logged, not raised."""

PIPELINE_PLAN_FAIL_TRANSITION_FAILED: Final[str] = (
    "pipeline.plan_review.fail_transition_failed"
)
"""The best-effort transition of the root task to FAILED could not be written."""

PIPELINE_PLAN_APPROVAL_PARK_FAILED: Final[str] = (
    "pipeline.plan_review.approval_park_failed"
)
"""Parking the plan's approval item failed; the plan is being marked FAILED."""

PIPELINE_PLAN_APPROVAL_RETIRE_FAILED: Final[str] = (
    "pipeline.plan_review.approval_retire_failed"
)
"""An approval parked before a failed park could not be removed; it outlives
its plan and is still actionable."""

PIPELINE_PLAN_REVIEW_PANEL_ATTACHED: Final[str] = "pipeline.plan_review.panel_attached"
"""The stakeholder plan-review panel was attached to the work pipeline."""

PIPELINE_PLAN_REVIEW_PANEL_FAILED: Final[str] = "pipeline.plan_review.panel_failed"
"""The stakeholder panel errored; the plan is parked for approval review-less."""

PIPELINE_ENTRY_UNKNOWN_SOURCE: Final[str] = "pipeline.entry.unknown_source"
"""No work-entry adapter is wired for the requested work source."""
