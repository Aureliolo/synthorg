"""Initiative-tail event constants.

The stages between "every plan item is done" and delivery: assembling the
verified pieces into one running deliverable, scoring that whole against the
objective's success criteria, and replanning an initiative that can no longer
advance. Distinct from ``events.project`` (the status rollup that opens the
tail) and ``events.retrospective`` (the consuming tail past delivery).
"""

from typing import Final

INITIATIVE_REPLAN_SCHEDULED: Final[str] = "initiative.replan.scheduled"
INITIATIVE_REPLAN_STARTED: Final[str] = "initiative.replan.started"
INITIATIVE_REPLAN_COMPLETED: Final[str] = "initiative.replan.completed"
INITIATIVE_REPLAN_SKIPPED: Final[str] = "initiative.replan.skipped"
INITIATIVE_REPLAN_FAILED: Final[str] = "initiative.replan.failed"

INITIATIVE_INTEGRATION_SCHEDULED: Final[str] = "initiative.integration.scheduled"
INITIATIVE_INTEGRATION_STARTED: Final[str] = "initiative.integration.started"
INITIATIVE_INTEGRATION_DISPATCHED: Final[str] = "initiative.integration.dispatched"
INITIATIVE_INTEGRATION_SKIPPED: Final[str] = "initiative.integration.skipped"
INITIATIVE_INTEGRATION_FAILED: Final[str] = "initiative.integration.failed"

INITIATIVE_EVALUATION_SCHEDULED: Final[str] = "initiative.evaluation.scheduled"
INITIATIVE_EVALUATION_STARTED: Final[str] = "initiative.evaluation.started"
INITIATIVE_EVALUATION_COMPLETED: Final[str] = "initiative.evaluation.completed"
INITIATIVE_EVALUATION_SKIPPED: Final[str] = "initiative.evaluation.skipped"
INITIATIVE_EVALUATION_FAILED: Final[str] = "initiative.evaluation.failed"
