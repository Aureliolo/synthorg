"""Goal / objective entry-adapter event constants."""

from typing import Final

OBJECTIVE_SUBMISSION_RECEIVED: Final[str] = "objective.submission.received"
OBJECTIVE_SUBMISSION_DISPATCHED: Final[str] = "objective.submission.dispatched"
OBJECTIVE_PROJECT_PROVISIONED: Final[str] = "objective.project.provisioned"
OBJECTIVE_PROJECT_ALREADY_EXISTS: Final[str] = "objective.project.already_exists"
OBJECTIVE_PIPELINE_FAILED: Final[str] = "objective.pipeline.failed"
OBJECTIVE_ENTRY_WIRED: Final[str] = "objective.entry.wired"
