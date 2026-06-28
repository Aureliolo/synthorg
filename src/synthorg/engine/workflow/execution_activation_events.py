# module-kind: adapter
"""Post-persistence activation observability for workflow execution.

Emits the activation event stream after a :class:`WorkflowExecution` is
saved: the domain activation event, the workflow status transition, a node
transition per created task node, and the terminal completion event +
metric for a task-less workflow that completes instantly. Kept separate
from ``execution_activation_helpers`` (graph walking / task config parsing)
so the observability concern is isolated and self-guarding.
"""

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.workflow.enums import (
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
)
from synthorg.engine.workflow.execution_models import WorkflowExecution
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workflow_execution import (
    WORKFLOW_EXEC_ACTIVATED,
    WORKFLOW_EXEC_ACTIVATION_EVENTS_FAILED,
    WORKFLOW_EXEC_COMPLETED,
    WORKFLOW_EXEC_NODE_STATUS_TRANSITIONED,
    WORKFLOW_EXEC_STATUS_TRANSITIONED,
)
from synthorg.observability.metrics_hub import record_workflow_execution

logger = get_logger(__name__)


def emit_activation_events(
    execution: WorkflowExecution,
    *,
    definition_id: str,
    task_count: int,
) -> None:
    """Emit post-persistence activation observability events (best-effort).

    The activation write is authoritative and has already committed when this
    runs, so observability must never abort it: a failure degrades to a
    warning rather than propagating.

    Raises:
        MemoryError: Re-raised unconditionally.
        RecursionError: Re-raised unconditionally.
    """
    try:
        _emit_activation_events(
            execution,
            definition_id=definition_id,
            task_count=task_count,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        # The warning rides the same logging/metrics path that just failed,
        # so it can raise for the same reason. Guard it too: the activation
        # write has already committed, and letting this escape would report
        # ``activate()`` failure for an execution that actually persisted.
        try:
            logger.warning(
                WORKFLOW_EXEC_ACTIVATION_EVENTS_FAILED,
                execution_id=str(execution.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        except Exception as warning_exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(warning_exc)


def _emit_activation_events(
    execution: WorkflowExecution,
    *,
    definition_id: str,
    task_count: int,
) -> None:
    """Emit the activation events.

    For a task-less workflow that completes instantly, ``created_at ==
    completed_at`` so the duration metric is exactly zero seconds.
    """
    execution_id = str(execution.id)
    logger.info(
        WORKFLOW_EXEC_ACTIVATED,
        execution_id=execution_id,
        definition_id=definition_id,
        task_count=task_count,
    )
    logger.info(
        WORKFLOW_EXEC_STATUS_TRANSITIONED,
        execution_id=execution_id,
        workflow_definition_id=definition_id,
        from_status=None,
        to_status=execution.status.value,
    )
    for node_exec in execution.node_executions:
        if node_exec.status != WorkflowNodeExecutionStatus.TASK_CREATED:
            continue
        logger.info(
            WORKFLOW_EXEC_NODE_STATUS_TRANSITIONED,
            execution_id=execution_id,
            workflow_definition_id=definition_id,
            node_id=node_exec.node_id,
            task_id=node_exec.task_id,
            from_status=None,
            to_status=WorkflowNodeExecutionStatus.TASK_CREATED.value,
        )
    if execution.status == WorkflowExecutionStatus.COMPLETED:
        logger.info(WORKFLOW_EXEC_COMPLETED, execution_id=execution_id)
        record_workflow_execution(
            workflow_definition_id=definition_id,
            status=WorkflowExecutionStatus.COMPLETED.value,
            duration_seconds=0.0,
        )
