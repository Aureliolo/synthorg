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
