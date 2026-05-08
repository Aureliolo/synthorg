"""Review pipeline and intake engine event constants."""

from typing import Final, LiteralString

REVIEW_PIPELINE_STARTED: Final[str] = "review.pipeline.started"
REVIEW_PIPELINE_STAGE_COMPLETED: Final[str] = "review.pipeline.stage.completed"
REVIEW_PIPELINE_COMPLETED: Final[str] = "review.pipeline.completed"
REVIEW_STAGE_DECIDED: Final[str] = "review.stage.decided"
REVIEW_TASK_LOOKUP_FAILED: Final[str] = "review.task.lookup_failed"
REVIEW_STAGE_LOOKUP_FAILED: Final[LiteralString] = "review.stage.lookup.failed"
INTAKE_REQUEST_RECEIVED: Final[str] = "intake.request.received"
INTAKE_REQUEST_ACCEPTED: Final[str] = "intake.request.accepted"
INTAKE_REQUEST_REJECTED: Final[str] = "intake.request.rejected"
INTAKE_DIRECT_TASK_CREATED: Final[str] = "intake.direct.task_created"
INTAKE_AGENT_PARSE_FAILED: Final[str] = "intake.agent.parse_failed"
INTAKE_AGENT_REFINED_INVALID: Final[str] = "intake.agent.refined_invalid"
INTAKE_AGENT_EMPTY_RESPONSE: Final[str] = "intake.agent.empty_response"
APPROVAL_GATE_PIPELINE_ALL_SKIPPED: Final[str] = "approval_gate.pipeline_all_skipped"
