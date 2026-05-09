"""Workflow execution event name constants for observability.

Covers activation, task creation, condition evaluation, and
lifecycle transitions of workflow execution instances.
"""

from typing import Final

# -- Activation events --------------------------------------------------------

WORKFLOW_EXEC_ACTIVATED: Final[str] = "workflow.execution.activated"
"""Workflow definition activated -- execution instance created."""

WORKFLOW_EXEC_INVALID_DEFINITION: Final[str] = "workflow.execution.invalid_definition"
"""Activation rejected -- workflow definition failed validation."""

WORKFLOW_EXEC_NOT_FOUND: Final[str] = "workflow.execution.not_found"
"""Workflow definition or execution instance not found."""

WORKFLOW_EXEC_INVALID_STATUS: Final[str] = "workflow.execution.invalid_status"
"""Execution exists but is in an unexpected status for the operation."""

# -- Node processing events ---------------------------------------------------

WORKFLOW_EXEC_TASK_CREATED: Final[str] = "workflow.execution.task_created"
"""Concrete task created from a TASK node."""

WORKFLOW_EXEC_NODE_SKIPPED: Final[str] = "workflow.execution.node_skipped"
"""Node skipped (conditional branch not taken)."""

WORKFLOW_EXEC_NODE_COMPLETED: Final[str] = "workflow.execution.node_completed"
"""Control node processed (START, END, SPLIT, JOIN, etc.)."""

WORKFLOW_EXEC_CONDITION_EVALUATED: Final[str] = "workflow.execution.condition_evaluated"
"""Conditional node expression evaluated."""

WORKFLOW_EXEC_CONDITION_EVAL_FAILED: Final[str] = (
    "workflow.execution.condition_eval_failed"
)
"""Conditional node expression evaluation failed."""

WORKFLOW_EXEC_NODE_TASK_COMPLETED: Final[str] = "workflow.execution.node_task_completed"
"""Task linked to a TASK node completed successfully."""

WORKFLOW_EXEC_NODE_TASK_FAILED: Final[str] = "workflow.execution.node_task_failed"
"""Task linked to a TASK node failed."""

# -- Lifecycle events ---------------------------------------------------------

WORKFLOW_EXEC_COMPLETED: Final[str] = "workflow.execution.completed"
"""Workflow execution completed -- all tasks finished."""

WORKFLOW_EXEC_FAILED: Final[str] = "workflow.execution.failed"
"""Workflow execution failed."""

WORKFLOW_EXEC_CANCELLED: Final[str] = "workflow.execution.cancelled"
"""Workflow execution cancelled by user (terminal success path)."""

WORKFLOW_EXEC_CANCEL_CONFLICT: Final[str] = "workflow.execution.cancel_conflict"
"""Cancellation rejected (already-terminal status / version conflict / etc.).

Emitted on the 409 path so audit / telemetry counters do not conflate
failed cancel attempts with successful cancellations.
"""

WORKFLOW_EXEC_STATUS_TRANSITIONED: Final[str] = "workflow.execution.status_transitioned"
"""Workflow execution status transitioned -- emitted on every persisted hop.

Complements terminal-state events above: the ``*_COMPLETED`` /
``*_FAILED`` / ``*_CANCELLED`` constants stay on the terminal hop and
remain the canonical "this is the final state" markers, while
``WORKFLOW_EXEC_STATUS_TRANSITIONED`` is the cross-hop audit-stream
event carrying ``from_status`` / ``to_status`` / ``execution_id`` /
``workflow_definition_id``. Today the only persisted transitions are
the three terminal-state hops (``RUNNING -> COMPLETED`` / ``->
FAILED`` / ``-> CANCELLED``); the bootstrap ``PENDING -> RUNNING``
state is set inline during initial execution creation in
``WorkflowExecutionService`` rather than as a separate persisted
transition, so no separate event is emitted for that hop."""

WORKFLOW_EXEC_NODE_STATUS_TRANSITIONED: Final[str] = (
    "workflow.execution.node_status_transitioned"
)
"""Workflow node execution status transitioned -- emitted on every hop."""

# -- Subworkflow runtime events ----------------------------------------------

WORKFLOW_EXEC_SUBWORKFLOW_FRAME_PUSHED: Final[str] = (
    "workflow.execution.subworkflow.frame_pushed"
)
"""Subworkflow frame pushed onto the execution stack."""

WORKFLOW_EXEC_SUBWORKFLOW_FRAME_POPPED: Final[str] = (
    "workflow.execution.subworkflow.frame_popped"
)
"""Subworkflow frame popped after child graph completed."""

WORKFLOW_EXEC_SUBWORKFLOW_DEPTH_EXCEEDED: Final[str] = (
    "workflow.execution.subworkflow.depth_exceeded"
)
"""Runtime subworkflow nesting depth exceeded the configured limit."""

WORKFLOW_EXEC_SUBWORKFLOW_NODE_COMPLETED: Final[str] = (
    "workflow.execution.subworkflow.node_completed"
)
"""A SUBWORKFLOW node finished walking its child graph."""

WORKFLOW_EXECUTION_USERNAME_FALLBACK: Final[str] = (
    "workflow.execution.username_fallback"
)
"""Workflow-execution caller resolved to a username fallback when the
session principal lacked a stable identifier."""
