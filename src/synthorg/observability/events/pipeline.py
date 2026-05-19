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
