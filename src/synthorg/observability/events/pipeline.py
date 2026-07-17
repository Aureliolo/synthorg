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

PIPELINE_PLAN_DECOMPOSITION_FAILED: Final[str] = (
    "pipeline.plan_review.decomposition_failed"
)
"""Decomposition failed; the plan shell was marked FAILED and the task failed."""

PIPELINE_PLAN_REVIEW_PANEL_ATTACHED: Final[str] = "pipeline.plan_review.panel_attached"
"""The stakeholder plan-review panel was attached to the work pipeline."""

PIPELINE_PLAN_REVIEW_PANEL_FAILED: Final[str] = "pipeline.plan_review.panel_failed"
"""The stakeholder panel errored; the plan is parked for approval review-less."""

PIPELINE_ENTRY_UNKNOWN_SOURCE: Final[str] = "pipeline.entry.unknown_source"
"""No work-entry adapter is wired for the requested work source."""
