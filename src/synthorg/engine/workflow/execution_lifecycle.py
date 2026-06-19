# module-kind: adapter
"""Workflow execution lifecycle transitions and task-event handling.

All functions operate on ``WorkflowExecution`` models and delegate
persistence to the injected repository.
"""

from datetime import UTC, datetime

from synthorg.core.pagination import DEFAULT_LIST_LIMIT
from synthorg.core.persistence_errors import (
    PersistenceVersionConflictError,
    RecordNotFoundError,
)
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.errors import (
    WorkflowExecutionAlreadyTerminalError,
    WorkflowExecutionError,
    WorkflowExecutionNotFoundError,
)
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.engine.workflow.enums import (
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.engine.workflow.execution_models import (
    WorkflowExecution,
    WorkflowNodeExecution,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.metrics import METRICS_CLOCK_SKEW_DETECTED
from synthorg.observability.events.workflow_execution import (
    WORKFLOW_EXEC_CANCEL_CONFLICT,
    WORKFLOW_EXEC_CANCELLED,
    WORKFLOW_EXEC_COMPLETED,
    WORKFLOW_EXEC_FAILED,
    WORKFLOW_EXEC_INVALID_STATUS,
    WORKFLOW_EXEC_NODE_STATUS_TRANSITIONED,
    WORKFLOW_EXEC_NODE_TASK_COMPLETED,
    WORKFLOW_EXEC_NODE_TASK_FAILED,
    WORKFLOW_EXEC_NOT_FOUND,
    WORKFLOW_EXEC_STATUS_TRANSITIONED,
)
from synthorg.observability.metrics_hub import record_workflow_execution
from synthorg.observability.tracing.instrumentation import get_tracer
from synthorg.persistence.workflow_execution_protocol import (
    WorkflowExecutionFilterSpec,
    WorkflowExecutionRepository,
)

logger = get_logger(__name__)
_tracer = get_tracer(__name__)

_TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED},
)


def _execution_duration_seconds(
    execution: WorkflowExecution,
    now: datetime,
) -> float:
    """Compute end-to-end duration from creation to terminal moment.

    Used for ``synthorg_workflow_execution_seconds`` histogram.
    Negative deltas (clock skew between nodes, NTP misconfig, VM
    time jumps) are clamped to 0 AND a WARN is emitted so an
    operator can spot the underlying clock issue instead of
    silently absorbing it into the 0-bucket.

    Returns:
        Non-negative duration in seconds; ``0.0`` when ``now`` is
        before ``execution.created_at`` (clock skew was logged).
    """
    duration = (now - execution.created_at).total_seconds()
    if duration < 0:
        logger.warning(
            METRICS_CLOCK_SKEW_DETECTED,
            execution_id=str(execution.id),
            skew_seconds=abs(duration),
            note=("completed_at < created_at; check NTP or multi-node clock sync"),
        )
        return 0.0
    return duration


# -- CRUD helpers ----------------------------------------------------------


async def get_execution(
    repo: WorkflowExecutionRepository,
    execution_id: str,
) -> WorkflowExecution | None:
    """Retrieve a workflow execution by ID.

    Returns:
        The matching :class:`WorkflowExecution`, or ``None`` if absent.
    """
    return await repo.get(execution_id)


async def list_executions(
    repo: WorkflowExecutionRepository,
    definition_id: str | None = None,
    *,
    status: WorkflowExecutionStatus | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> tuple[WorkflowExecution, ...]:
    """List executions filtered by definition and/or status (bounded by *limit*).

    Returns:
        Executions matching the supplied filters (up to ``limit``).
    """
    return await repo.query(
        WorkflowExecutionFilterSpec(definition_id=definition_id, status=status),
        limit=limit,
    )


async def cancel_execution(
    repo: WorkflowExecutionRepository,
    execution_id: str,
    *,
    cancelled_by: str,
) -> WorkflowExecution:
    """Cancel a workflow execution.

    Returns:
        The cancelled :class:`WorkflowExecution` (status ``CANCELLED``,
        completion timestamp set, version bumped).

    Raises:
        WorkflowExecutionNotFoundError: If not found.
        WorkflowExecutionAlreadyTerminalError: If execution is already
            terminal. Maps to 409 +
            ``WORKFLOW_EXECUTION_ALREADY_TERMINAL`` so clients can tell
            "execution finished before cancel arrived" (no retry will
            succeed) apart from a row-level optimistic-concurrency
            race.
        PersistenceVersionConflictError: Re-raised from the save path
            when a concurrent writer mutated the row between the read
            and the cancel write. Callers should distinguish this from
            ``WorkflowExecutionAlreadyTerminalError``: the persistence
            race is retryable (re-read, re-issue), the terminal-status
            case is not.
    """
    execution = await repo.get(execution_id)
    if execution is None:
        logger.warning(
            WORKFLOW_EXEC_NOT_FOUND,
            execution_id=execution_id,
        )
        msg = f"Workflow execution {execution_id!r} not found"
        raise WorkflowExecutionNotFoundError(msg)

    terminal_statuses = {
        WorkflowExecutionStatus.COMPLETED,
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.CANCELLED,
    }
    if execution.status in terminal_statuses:
        msg = (
            f"Cannot cancel execution {execution_id!r}"
            f" in terminal status {execution.status.value!r}"
        )
        logger.warning(
            WORKFLOW_EXEC_CANCEL_CONFLICT,
            execution_id=execution_id,
            current_status=execution.status.value,
            error=msg,
        )
        raise WorkflowExecutionAlreadyTerminalError(msg)

    with _tracer.start_as_current_span(
        "workflow.execution.cancelled",
        attributes={
            "workflow.definition_id": execution.definition_id,
            "workflow.execution_id": str(execution.id),
            "workflow.cancelled_by": cancelled_by,
        },
    ):
        now = datetime.now(UTC)
        cancelled = execution.model_copy(
            update={
                "status": WorkflowExecutionStatus.CANCELLED,
                "updated_at": now,
                "completed_at": now,
                "version": execution.version + 1,
            }
        )
        try:
            await repo.save(cancelled)
        except PersistenceVersionConflictError as exc:
            # Optimistic-concurrency race: another writer mutated the
            # execution between the read above and this save. Re-fetch
            # so the audit signal records the *winner's* status, not the
            # stale pre-save snapshot (still 'running' even after another
            # writer moved the row to a terminal state).
            refreshed = await repo.get(execution_id)
            logger.warning(
                WORKFLOW_EXEC_CANCEL_CONFLICT,
                execution_id=execution_id,
                current_status=(
                    refreshed.status.value
                    if refreshed is not None
                    else execution.status.value
                ),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        # State-transition logs fire AFTER persistence succeeds so
        # the audit trail only records transitions that actually
        # happened. A save failure raises, skipping these logs.
        logger.info(
            WORKFLOW_EXEC_STATUS_TRANSITIONED,
            execution_id=execution_id,
            workflow_definition_id=execution.definition_id,
            from_status=execution.status.value,
            to_status=WorkflowExecutionStatus.CANCELLED.value,
        )
        logger.info(
            WORKFLOW_EXEC_CANCELLED,
            execution_id=execution_id,
            cancelled_by=cancelled_by,
        )
        record_workflow_execution(
            workflow_definition_id=execution.definition_id,
            status=WorkflowExecutionStatus.CANCELLED.value,
            duration_seconds=_execution_duration_seconds(execution, now),
        )

    return cancelled


async def complete_execution(
    repo: WorkflowExecutionRepository,
    execution_id: str,
) -> WorkflowExecution:
    """Transition a running execution to COMPLETED.

    Returns:
        The updated execution with ``status=COMPLETED``.

    Raises:
        WorkflowExecutionNotFoundError: If not found.
        WorkflowExecutionError: If execution is not RUNNING.
    """
    execution = await _load_running(repo, execution_id)
    with _tracer.start_as_current_span(
        "workflow.execution.completed",
        attributes={
            "workflow.definition_id": execution.definition_id,
            "workflow.execution_id": str(execution.id),
        },
    ):
        now = datetime.now(UTC)
        completed = execution.model_copy(
            update={
                "status": WorkflowExecutionStatus.COMPLETED,
                "updated_at": now,
                "completed_at": now,
                "version": execution.version + 1,
            },
        )
        await repo.save(completed)
        logger.info(
            WORKFLOW_EXEC_STATUS_TRANSITIONED,
            execution_id=execution_id,
            workflow_definition_id=execution.definition_id,
            from_status=execution.status.value,
            to_status=WorkflowExecutionStatus.COMPLETED.value,
        )
        logger.info(
            WORKFLOW_EXEC_COMPLETED,
            execution_id=execution_id,
        )
        record_workflow_execution(
            workflow_definition_id=execution.definition_id,
            status=WorkflowExecutionStatus.COMPLETED.value,
            duration_seconds=_execution_duration_seconds(execution, now),
        )
    return completed


async def fail_execution(
    repo: WorkflowExecutionRepository,
    execution_id: str,
    *,
    error: str,
) -> WorkflowExecution:
    """Transition a running execution to FAILED.

    Returns:
        The updated execution with ``status=FAILED`` and error stored.

    Raises:
        WorkflowExecutionNotFoundError: If not found.
        WorkflowExecutionError: If execution is not RUNNING.
    """
    execution = await _load_running(repo, execution_id)
    with _tracer.start_as_current_span(
        "workflow.execution.failed",
        attributes={
            "workflow.definition_id": execution.definition_id,
            "workflow.execution_id": str(execution.id),
            # Don't attach the raw ``error`` string as a span
            # attribute: error messages are unbounded user / model
            # output and would inflate the trace cardinality and
            # storage cost. The full message is already on the
            # post-save ``WORKFLOW_EXEC_FAILED`` log payload via
            # ``_emit_terminal_workflow_observability``; trace
            # consumers cross-reference via ``workflow.execution_id``.
        },
    ):
        now = datetime.now(UTC)
        failed = execution.model_copy(
            update={
                "status": WorkflowExecutionStatus.FAILED,
                "error": error,
                "updated_at": now,
                "completed_at": now,
                "version": execution.version + 1,
            },
        )
        await repo.save(failed)
        logger.info(
            WORKFLOW_EXEC_STATUS_TRANSITIONED,
            execution_id=execution_id,
            workflow_definition_id=execution.definition_id,
            from_status=execution.status.value,
            to_status=WorkflowExecutionStatus.FAILED.value,
            error=error,
        )
        logger.info(
            WORKFLOW_EXEC_FAILED,
            execution_id=execution_id,
            error=error,
        )
        record_workflow_execution(
            workflow_definition_id=execution.definition_id,
            status=WorkflowExecutionStatus.FAILED.value,
            duration_seconds=_execution_duration_seconds(execution, now),
        )
    return failed


# -- Task-event handling ---------------------------------------------------


def _retry_event_for(event: TaskStateChanged) -> str:
    """Return the WORKFLOW_EXEC_NODE_TASK_* event for a retry log line."""
    if event.new_status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
        return WORKFLOW_EXEC_NODE_TASK_FAILED
    return WORKFLOW_EXEC_NODE_TASK_COMPLETED


async def _dispatch_task_handler(
    repo: WorkflowExecutionRepository,
    execution: WorkflowExecution,
    event: TaskStateChanged,
) -> None:
    """Route a terminal task event to the matching lifecycle handler."""
    if event.new_status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
        await _handle_task_failed(repo, execution, event)
    else:
        await _handle_task_completed(repo, execution, event)


def _expected_terminal_node_status(
    event: TaskStateChanged,
) -> WorkflowNodeExecutionStatus:
    """Return the node status the lifecycle handler would set for ``event``."""
    if event.new_status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
        return WorkflowNodeExecutionStatus.TASK_FAILED
    return WorkflowNodeExecutionStatus.TASK_COMPLETED


async def _retry_after_version_conflict(
    repo: WorkflowExecutionRepository,
    execution: WorkflowExecution,
    event: TaskStateChanged,
) -> None:
    """Refresh the execution after a conflict and re-dispatch idempotently."""
    retry_event = _retry_event_for(event)
    logger.warning(
        retry_event,
        execution_id=str(execution.id),
        task_id=event.task_id,
        error="Concurrent modification; re-fetching execution",
    )
    refreshed = await repo.get(str(execution.id))
    if refreshed is None:
        logger.warning(
            retry_event,
            execution_id=str(execution.id),
            task_id=event.task_id,
            error="Execution not found after version conflict",
        )
        return
    if refreshed.status in {
        WorkflowExecutionStatus.COMPLETED,
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.CANCELLED,
    }:
        return
    # Idempotency: if the winning writer already applied the same
    # node transition this event would set, re-dispatching would
    # bump version + emit a bogus same-status hop.
    if _node_status_for_task(refreshed, event.task_id) is (
        _expected_terminal_node_status(event)
    ):
        return
    try:
        await _dispatch_task_handler(repo, refreshed, event)
    except RecordNotFoundError:
        logger.warning(
            retry_event,
            execution_id=str(execution.id),
            task_id=event.task_id,
            error="Execution deleted during retry",
        )
    except PersistenceVersionConflictError:
        logger.warning(
            retry_event,
            execution_id=str(execution.id),
            task_id=event.task_id,
            error="Concurrent modification during retry",
        )


async def handle_task_state_changed(
    repo: WorkflowExecutionRepository,
    event: TaskStateChanged,
) -> None:
    """React to a task state change from the TaskEngine.

    Correlates the task to a running workflow execution and
    transitions the execution to COMPLETED or FAILED as
    appropriate.
    """
    if event.mutation_type != "transition":
        return
    if event.new_status not in _TERMINAL_TASK_STATUSES:
        return

    execution = await repo.find_by_task_id(event.task_id)
    if execution is None:
        return

    try:
        await _dispatch_task_handler(repo, execution, event)
    except RecordNotFoundError:
        # Execution row was deleted between read and update; retry
        # would only fail the same way, so log and drop.
        logger.warning(
            _retry_event_for(event),
            execution_id=str(execution.id),
            task_id=event.task_id,
            error="Execution deleted before lifecycle update could persist",
        )
    except PersistenceVersionConflictError:
        await _retry_after_version_conflict(repo, execution, event)


# -- Private helpers -------------------------------------------------------


def _log_node_status_transition(
    execution_id: str,
    workflow_definition_id: str,
    task_id: str,
    previous_node_status: WorkflowNodeExecutionStatus | None,
    to_node_status: WorkflowNodeExecutionStatus,
) -> None:
    """Emit ``WORKFLOW_EXEC_NODE_STATUS_TRANSITIONED`` for a node hop."""
    logger.info(
        WORKFLOW_EXEC_NODE_STATUS_TRANSITIONED,
        execution_id=execution_id,
        workflow_definition_id=workflow_definition_id,
        task_id=task_id,
        from_status=previous_node_status.value
        if previous_node_status is not None
        else None,
        to_status=to_node_status.value,
    )


def _emit_terminal_workflow_observability(
    *,
    execution: WorkflowExecution,
    terminal_status: WorkflowExecutionStatus,
    now: datetime,
    event_constant: str,
    extra_event_kwargs: dict[str, str] | None = None,
) -> None:
    """Emit the post-save state-transition log + terminal log + metric.

    Centralizes the four-step observability tail shared by both
    task-driven terminal handlers (``_handle_task_failed`` and the
    completion branch of ``_handle_task_completed``) so a future
    workflow-observability change does not need to be applied twice.
    """
    logger.info(
        WORKFLOW_EXEC_STATUS_TRANSITIONED,
        execution_id=str(execution.id),
        workflow_definition_id=execution.definition_id,
        from_status=execution.status.value,
        to_status=terminal_status.value,
        **(extra_event_kwargs or {}),
    )
    logger.info(
        event_constant,
        execution_id=str(execution.id),
        **(extra_event_kwargs or {}),
    )
    record_workflow_execution(
        workflow_definition_id=execution.definition_id,
        status=terminal_status.value,
        duration_seconds=_execution_duration_seconds(execution, now),
    )


async def _handle_task_failed(
    repo: WorkflowExecutionRepository,
    execution: WorkflowExecution,
    event: TaskStateChanged,
) -> None:
    """Handle a task failure or cancellation event.

    Wrapped in an OTLP span so cascading task-driven terminal
    transitions are visible in traces alongside the direct
    ``cancel_execution`` / ``fail_execution`` paths (which already
    span themselves).
    """
    previous_node_status = _node_status_for_task(execution, event.task_id)
    new_status_value = (
        event.new_status.value if event.new_status is not None else "unknown"
    )
    with _tracer.start_as_current_span(
        "workflow.execution.task_failed",
        attributes={
            "workflow.definition_id": execution.definition_id,
            "workflow.execution_id": str(execution.id),
            "workflow.terminal_via": "task_failed",
            "task.id": event.task_id,
            "task.new_status": new_status_value,
        },
    ):
        updated = _update_node_status(
            execution,
            event.task_id,
            WorkflowNodeExecutionStatus.TASK_FAILED,
        )
        now = datetime.now(UTC)
        verb = "cancelled" if event.new_status is TaskStatus.CANCELLED else "failed"
        error_msg = f"Task {event.task_id} {verb}"
        failed = updated.model_copy(
            update={
                "status": WorkflowExecutionStatus.FAILED,
                "error": error_msg,
                "updated_at": now,
                "completed_at": now,
            },
        )
        await repo.save(failed)
        # State-transition logs fire AFTER persistence succeeds. Save
        # raises propagate here, skipping these logs and the metric.
        _log_node_status_transition(
            execution_id=str(execution.id),
            workflow_definition_id=execution.definition_id,
            task_id=event.task_id,
            previous_node_status=previous_node_status,
            to_node_status=WorkflowNodeExecutionStatus.TASK_FAILED,
        )
        logger.info(
            WORKFLOW_EXEC_NODE_TASK_FAILED,
            execution_id=str(execution.id),
            task_id=event.task_id,
        )
        _emit_terminal_workflow_observability(
            execution=execution,
            terminal_status=WorkflowExecutionStatus.FAILED,
            now=now,
            event_constant=WORKFLOW_EXEC_FAILED,
            extra_event_kwargs={"error": error_msg},
        )


async def _handle_task_completed(
    repo: WorkflowExecutionRepository,
    execution: WorkflowExecution,
    event: TaskStateChanged,
) -> None:
    """Handle a task completion event.

    When this completion is the last outstanding task and drives the
    workflow to its terminal state, the persistence + log + metric
    block is wrapped in an OTLP span so cascading task-driven
    terminal transitions appear in traces alongside the direct
    ``complete_execution`` path.
    """
    previous_node_status = _node_status_for_task(execution, event.task_id)
    updated = _update_node_status(
        execution,
        event.task_id,
        WorkflowNodeExecutionStatus.TASK_COMPLETED,
    )
    if _all_tasks_completed(updated):
        await _finalize_task_completed_terminal(
            repo,
            execution,
            updated,
            event,
            previous_node_status,
        )
    else:
        await repo.save(updated)
        # Node-status log fires AFTER persistence; the workflow
        # itself stays RUNNING so no terminal log is emitted here.
        _log_node_status_transition(
            execution_id=str(execution.id),
            workflow_definition_id=execution.definition_id,
            task_id=event.task_id,
            previous_node_status=previous_node_status,
            to_node_status=WorkflowNodeExecutionStatus.TASK_COMPLETED,
        )
        logger.info(
            WORKFLOW_EXEC_NODE_TASK_COMPLETED,
            execution_id=str(execution.id),
            task_id=event.task_id,
        )


async def _finalize_task_completed_terminal(
    repo: WorkflowExecutionRepository,
    execution: WorkflowExecution,
    updated: WorkflowExecution,
    event: TaskStateChanged,
    previous_node_status: WorkflowNodeExecutionStatus | None,
) -> None:
    """Persist + log + record metric for the terminal completion path.

    Split out from :func:`_handle_task_completed` so that handler
    stays under the 50-line ceiling. Wraps the full save / state-
    transition log / metric block in an OTLP span keyed at
    ``workflow.execution.task_completed`` so cascading completions
    appear in traces.
    """
    with _tracer.start_as_current_span(
        "workflow.execution.task_completed",
        attributes={
            "workflow.definition_id": execution.definition_id,
            "workflow.execution_id": str(execution.id),
            "workflow.terminal_via": "task_completed",
            "task.id": event.task_id,
        },
    ):
        now = datetime.now(UTC)
        completed = updated.model_copy(
            update={
                "status": WorkflowExecutionStatus.COMPLETED,
                "updated_at": now,
                "completed_at": now,
            },
        )
        await repo.save(completed)
        # State-transition logs fire AFTER persistence succeeds.
        _log_node_status_transition(
            execution_id=str(execution.id),
            workflow_definition_id=execution.definition_id,
            task_id=event.task_id,
            previous_node_status=previous_node_status,
            to_node_status=WorkflowNodeExecutionStatus.TASK_COMPLETED,
        )
        logger.info(
            WORKFLOW_EXEC_NODE_TASK_COMPLETED,
            execution_id=str(execution.id),
            task_id=event.task_id,
        )
        _emit_terminal_workflow_observability(
            execution=execution,
            terminal_status=WorkflowExecutionStatus.COMPLETED,
            now=now,
            event_constant=WORKFLOW_EXEC_COMPLETED,
        )


async def _load_running(
    repo: WorkflowExecutionRepository,
    execution_id: str,
) -> WorkflowExecution:
    """Load an execution and validate it is RUNNING.

    Returns:
        The :class:`WorkflowExecution` confirmed to be in the
        ``RUNNING`` status.

    Raises:
        WorkflowExecutionNotFoundError: If no record matches.
        WorkflowExecutionError: If the execution exists but is not
            in ``RUNNING`` status.
    """
    execution = await repo.get(execution_id)
    if execution is None:
        logger.warning(
            WORKFLOW_EXEC_NOT_FOUND,
            execution_id=execution_id,
        )
        msg = f"Workflow execution {execution_id!r} not found"
        raise WorkflowExecutionNotFoundError(msg)

    if execution.status is not WorkflowExecutionStatus.RUNNING:
        msg = (
            f"Cannot transition execution {execution_id!r}"
            f" in status {execution.status.value!r}"
            " (expected 'running')"
        )
        logger.warning(
            WORKFLOW_EXEC_INVALID_STATUS,
            execution_id=execution_id,
            current_status=execution.status.value,
            error=msg,
        )
        raise WorkflowExecutionError(msg)

    return execution


def _node_status_for_task(
    execution: WorkflowExecution,
    task_id: str,
) -> WorkflowNodeExecutionStatus | None:
    """Return the current node-execution status for *task_id* if any."""
    for ne in execution.node_executions:
        if ne.task_id == task_id:
            return ne.status
    return None


def _update_node_status(
    execution: WorkflowExecution,
    task_id: str,
    new_status: WorkflowNodeExecutionStatus,
) -> WorkflowExecution:
    """Return a copy with one node's status updated.

    Returns:
        A new :class:`WorkflowExecution` whose ``node_executions``
        tuple replaces the matching node's status, with
        ``updated_at`` refreshed and the version bumped.

    Raises:
        ValueError: If no node matches ``task_id``.
    """
    found = False
    updated_nodes: list[WorkflowNodeExecution] = []
    for ne in execution.node_executions:
        if ne.task_id == task_id:
            updated_nodes.append(
                ne.model_copy(update={"status": new_status}),
            )
            found = True
        else:
            updated_nodes.append(ne)

    if not found:
        msg = f"task_id {task_id!r} not found in execution {execution.id!r}"
        logger.warning(
            WORKFLOW_EXEC_NOT_FOUND,
            execution_id=str(execution.id),
            task_id=task_id,
            error=msg,
        )
        raise ValueError(msg)

    return execution.model_copy(
        update={
            "node_executions": tuple(updated_nodes),
            "updated_at": datetime.now(UTC),
            "version": execution.version + 1,
        },
    )


def _all_tasks_completed(execution: WorkflowExecution) -> bool:
    """Check if all non-skipped executable nodes have completed.

    Returns:
        ``True`` when every non-skipped TASK / SUBWORKFLOW node has
        reached its respective ``COMPLETED`` status; ``False`` when
        any such node is still pending.
    """
    for ne in execution.node_executions:
        if ne.status is WorkflowNodeExecutionStatus.SKIPPED:
            continue
        if (
            ne.node_type is WorkflowNodeType.TASK
            and ne.status is not WorkflowNodeExecutionStatus.TASK_COMPLETED
        ):
            return False
        if (
            ne.node_type is WorkflowNodeType.SUBWORKFLOW
            and ne.status is not WorkflowNodeExecutionStatus.SUBWORKFLOW_COMPLETED
        ):
            return False
    return True
