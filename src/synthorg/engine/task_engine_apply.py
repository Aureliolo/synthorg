"""Mutation application logic for TaskEngine.

Each ``apply_*`` function takes the mutation, a persistence backend,
and a :class:`VersionTracker`, returning a :class:`TaskMutationResult`.
Extracted from ``task_engine.py`` to keep the main module focused on
lifecycle, queue management, and the public API.
"""

from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Mapping

from pydantic import ValidationError as PydanticValidationError

from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task
from synthorg.engine.errors import TaskVersionConflictError
from synthorg.engine.task_engine_models import (
    CancelTaskMutation,
    CreateTaskMutation,
    DeleteTaskMutation,
    TaskMutation,
    TaskMutationResult,
    TransitionTaskMutation,
    UpdateTaskMutation,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.task_engine import (
    TASK_ENGINE_MUTATION_APPLIED,
    TASK_ENGINE_MUTATION_FAILED,
    TASK_ENGINE_STATUS_TRANSITIONED,
    TASK_ENGINE_TIMING_FALLBACK,
)
from synthorg.observability.metrics_hub import record_task_run
from synthorg.observability.tracing.instrumentation import get_tracer

_tracer = get_tracer(__name__)

# Mapping from recorded TaskStatus values to the bounded outcome
# vocabulary expected by ``synthorg_task_runs_total`` /
# ``synthorg_task_duration_seconds`` (``VALID_TASK_OUTCOMES``).
# Wrapped in MappingProxyType so a misbehaving import-site cannot
# mutate the registry at runtime.
#
# Includes both truly terminal statuses (COMPLETED / CANCELLED /
# REJECTED) and the non-terminal FAILED hop. FAILED is recorded
# because a failed task can be reassigned and re-run, and operator
# dashboards want to see every failure event (a rate-of-failures
# query should see every failure, not just the last). REJECTED can
# only fire from CREATED (per ``task_transitions.py``) but is still
# a meaningful outcome to count.
#
# Naming note: this map is "recorded outcomes for the task-run
# metric", NOT "task statuses that mean the task is done forever".
# Use ``_TRULY_TERMINAL_STATUSES`` below when you need the latter
# (e.g. for deciding whether to clear the timing tracker).
_RECORDED_STATUS_OUTCOME: Mapping[TaskStatus, str] = MappingProxyType(
    {
        TaskStatus.COMPLETED: "succeeded",
        TaskStatus.FAILED: "failed",
        TaskStatus.CANCELLED: "cancelled",
        TaskStatus.REJECTED: "rejected",
    },
)
# Statuses where the creation-timestamp entry can be safely dropped
# from ``TaskTimingTracker``. ``FAILED`` is excluded because the
# engine may retry a failed task; the retry's duration metric should
# still be measured from the original creation, not from "now -
# nothing" (which would degrade to the missing-timestamp WARN
# fallback every retry).
_TRULY_TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.REJECTED},
)


def _compute_task_duration_sec(
    timings: TaskTimingTracker,
    task_id: str,
    mutation_type: str,
) -> float | None:
    """Look up *task_id*'s creation time and return ``now - created_at``.

    Returns ``None`` when the timing tracker has no record (typically
    a task created before the current process restart). Callers must
    skip the duration-histogram observation in that case so the
    histogram is not skewed by spurious 0-duration samples; the
    outcome counter still ticks so a tracked-since-restart vs.
    inherited-from-prior-process task can be told apart in dashboards
    via ``rate(task_runs_total) - rate(task_duration_count)``. A WARN
    with ``reason="creation_timestamp_missing"`` makes the missing-
    timestamp event searchable.
    """
    created_at = timings.get_creation(task_id)
    if created_at is not None:
        return max(0.0, (datetime.now(UTC) - created_at).total_seconds())
    logger.warning(
        TASK_ENGINE_TIMING_FALLBACK,
        mutation_type=mutation_type,
        task_id=task_id,
        reason="creation_timestamp_missing",
        note=(
            "duration-histogram observation skipped; "
            "task likely created before process restart"
        ),
    )
    return None


if TYPE_CHECKING:
    from synthorg.engine.task_engine_version import (
        TaskTimingTracker,
        VersionTracker,
    )
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


# ── Helpers ──────────────────────────────────────────────────────


def _format_validation_error(
    prefix: str,
    exc: PydanticValidationError,
) -> str:
    """Format a Pydantic validation error for external consumption.

    Extracts field paths and messages without exposing raw input
    values or internal Pydantic URL hints.
    """
    parts = [
        f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()
    ]
    return f"{prefix}: {'; '.join(parts)}"


def _not_found_result(
    mutation_type: str,
    request_id: str,
    task_id: str,
) -> TaskMutationResult:
    """Build a failure result for a missing task and log it.

    Sets ``error_code='not_found'`` on the result.
    """
    error = f"Task {task_id!r} not found"
    logger.warning(
        TASK_ENGINE_MUTATION_FAILED,
        mutation_type=mutation_type,
        request_id=request_id,
        task_id=task_id,
        error=error,
    )
    return TaskMutationResult(
        request_id=request_id,
        success=False,
        error=error,
        error_code="not_found",
    )


# ── Dispatch ─────────────────────────────────────────────────────


async def dispatch(
    mutation: TaskMutation,
    persistence: PersistenceBackend,
    versions: VersionTracker,
    timings: TaskTimingTracker,
) -> TaskMutationResult:
    """Dispatch and apply a mutation by type.

    Raises:
        TypeError: If the mutation type is unrecognised.
    """
    match mutation:
        case CreateTaskMutation():
            return await apply_create(mutation, persistence, versions, timings)
        case UpdateTaskMutation():
            return await apply_update(mutation, persistence, versions)
        case TransitionTaskMutation():
            return await apply_transition(mutation, persistence, versions, timings)
        case DeleteTaskMutation():
            return await apply_delete(mutation, persistence, versions, timings)
        case CancelTaskMutation():
            return await apply_cancel(mutation, persistence, versions, timings)
        case _:
            msg = f"Unknown mutation type: {type(mutation).__name__}"  # type: ignore[unreachable]
            raise TypeError(msg)


# ── Apply methods ────────────────────────────────────────────────


async def apply_create(
    mutation: CreateTaskMutation,
    persistence: PersistenceBackend,
    versions: VersionTracker,
    timings: TaskTimingTracker,
) -> TaskMutationResult:
    """Create a new task.

    Args:
        mutation: Creation request with task data.
        persistence: Backend for task storage.
        versions: Version tracker for optimistic concurrency.
        timings: Creation-time tracker; stamps ``task_id`` with the
            current UTC time so terminal transitions can compute
            duration for ``synthorg_task_runs_total`` /
            ``synthorg_task_duration_seconds``.

    Returns:
        Result with the created task on success, or a validation
        failure if the task data is invalid.
    """
    data = mutation.task_data
    task_id = f"task-{uuid4().hex}"

    try:
        task = Task(
            id=task_id,
            title=data.title,
            description=data.description,
            type=data.type,
            priority=data.priority,
            project=data.project,
            created_by=data.created_by,
            assigned_to=data.assigned_to,
            dependencies=data.dependencies,
            estimated_complexity=data.estimated_complexity,
            budget_limit=data.budget_limit,
        )
    except PydanticValidationError as exc:
        error_msg = _format_validation_error("Invalid task data", exc)
        logger.warning(
            TASK_ENGINE_MUTATION_FAILED,
            mutation_type="create",
            request_id=mutation.request_id,
            error=error_msg,
        )
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=False,
            error=error_msg,
            error_code="validation",
        )
    await persistence.tasks.save(task)
    versions.set_initial(task_id, 1)
    timings.record_creation(task_id, datetime.now(UTC))

    logger.info(
        TASK_ENGINE_MUTATION_APPLIED,
        mutation_type="create",
        request_id=mutation.request_id,
        task_id=task_id,
    )
    return TaskMutationResult(
        request_id=mutation.request_id,
        success=True,
        task=task,
        version=1,
    )


async def apply_update(
    mutation: UpdateTaskMutation,
    persistence: PersistenceBackend,
    versions: VersionTracker,
) -> TaskMutationResult:
    """Update task fields.

    Args:
        mutation: Update request with field-value pairs.
        persistence: Backend for task storage.
        versions: Version tracker for optimistic concurrency.

    Returns:
        Result with the updated task on success, or a failure with
        ``error_code`` of ``"not_found"``, ``"version_conflict"``,
        or ``"validation"``.
    """
    task = await persistence.tasks.get(mutation.task_id)
    if task is None:
        return _not_found_result("update", mutation.request_id, mutation.task_id)

    try:
        versions.check(mutation.task_id, mutation.expected_version)
    except TaskVersionConflictError as exc:
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=False,
            error=safe_error_description(exc),
            error_code="version_conflict",
        )

    if not mutation.updates:
        version = versions.get(mutation.task_id)
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=True,
            task=task,
            version=version,
            previous_status=task.status,
        )

    merged = task.model_dump()
    # mutation.updates is already deep-copied + wrapped in MappingProxyType
    # at construction time, so no second deep-copy needed here.
    merged.update(mutation.updates)
    try:
        updated = Task.model_validate(merged)
    except PydanticValidationError as exc:
        error_msg = _format_validation_error("Invalid update data", exc)
        logger.warning(
            TASK_ENGINE_MUTATION_FAILED,
            mutation_type="update",
            request_id=mutation.request_id,
            task_id=mutation.task_id,
            error=error_msg,
        )
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=False,
            error=error_msg,
            error_code="validation",
        )
    await persistence.tasks.save(updated)
    version = versions.bump(mutation.task_id)

    logger.info(
        TASK_ENGINE_MUTATION_APPLIED,
        mutation_type="update",
        request_id=mutation.request_id,
        task_id=mutation.task_id,
        fields=list(mutation.updates),
    )
    return TaskMutationResult(
        request_id=mutation.request_id,
        success=True,
        task=updated,
        version=version,
        previous_status=task.status,
    )


async def apply_transition(
    mutation: TransitionTaskMutation,
    persistence: PersistenceBackend,
    versions: VersionTracker,
    timings: TaskTimingTracker,
) -> TaskMutationResult:
    """Perform a task status transition.

    Args:
        mutation: Transition request with target status and reason.
        persistence: Backend for task storage.
        versions: Version tracker for optimistic concurrency.
        timings: Creation-time tracker; consulted on terminal
            transitions to compute duration for
            ``synthorg_task_runs_total`` /
            ``synthorg_task_duration_seconds``.

    Returns:
        Result with the transitioned task on success, or a failure
        with ``error_code`` of ``"not_found"``,
        ``"version_conflict"``, or ``"validation"``.
    """
    task = await persistence.tasks.get(mutation.task_id)
    if task is None:
        return _not_found_result("transition", mutation.request_id, mutation.task_id)

    try:
        versions.check(mutation.task_id, mutation.expected_version)
    except TaskVersionConflictError as exc:
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=False,
            error=safe_error_description(exc),
            error_code="version_conflict",
        )

    previous_status = task.status

    with _tracer.start_as_current_span(
        "task.transition",
        attributes={
            "task.id": mutation.task_id,
            "task.status.from": previous_status.value,
            "task.status.to": mutation.target_status.value,
            # Don't attach the free-form ``mutation.reason`` as a span
            # attribute: it can carry arbitrary user / model output and
            # would inflate trace cardinality. The reason is already on
            # the ``TASK_ENGINE_MUTATION_APPLIED`` log via the kwarg
            # ``reason=mutation.reason`` below; trace consumers
            # cross-reference via ``task.id``.
        },
    ):
        try:
            updated = task.with_transition(
                mutation.target_status,
                **mutation.overrides,
            )
        except ValueError as exc:
            logger.warning(
                TASK_ENGINE_MUTATION_FAILED,
                mutation_type="transition",
                request_id=mutation.request_id,
                task_id=mutation.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return TaskMutationResult(
                request_id=mutation.request_id,
                success=False,
                error=safe_error_description(exc),
                error_code="validation",
            )

        await persistence.tasks.save(updated)
        version = versions.bump(mutation.task_id)

        logger.info(
            TASK_ENGINE_MUTATION_APPLIED,
            mutation_type="transition",
            request_id=mutation.request_id,
            task_id=mutation.task_id,
            from_status=previous_status.value,
            to_status=mutation.target_status.value,
            reason=mutation.reason,
        )
        # Domain-scoped state-transition log for every persisted
        # task status hop. Emitted alongside the mutation-applied
        # log so the audit stream has a stable, mutation-shape-
        # independent entry keyed by from/to status; CLAUDE.md's
        # "every persisted hop" rule applies to the task subsystem
        # the same way it does to workflow / approval / pruning.
        logger.info(
            TASK_ENGINE_STATUS_TRANSITIONED,
            request_id=mutation.request_id,
            task_id=mutation.task_id,
            from_status=previous_status.value,
            to_status=mutation.target_status.value,
        )

    # Emit the recorded-outcome metric only on hops that map to a
    # bounded outcome (see ``_RECORDED_STATUS_OUTCOME``), so a
    # CREATED -> ASSIGNED hop doesn't pollute the counter. The
    # duration baseline is the engine's recorded creation time;
    # tasks created before a process restart have no entry, in which
    # case ``_compute_task_duration_sec`` returns ``None`` and
    # ``record_task_run`` skips the duration-histogram observation
    # while still incrementing the outcome counter (the histogram
    # is therefore not skewed by spurious 0-second samples).
    if mutation.target_status in _RECORDED_STATUS_OUTCOME:
        record_task_run(
            outcome=_RECORDED_STATUS_OUTCOME[mutation.target_status],
            duration_sec=_compute_task_duration_sec(
                timings,
                mutation.task_id,
                "transition",
            ),
        )
        # Free the timing entry only on truly terminal statuses
        # (COMPLETED / CANCELLED / REJECTED). FAILED stays because
        # the engine may retry the task, and the retry's duration
        # should still measure from the original creation.
        if mutation.target_status in _TRULY_TERMINAL_STATUSES:
            timings.remove(mutation.task_id)

    return TaskMutationResult(
        request_id=mutation.request_id,
        success=True,
        task=updated,
        version=version,
        previous_status=previous_status,
    )


async def apply_delete(
    mutation: DeleteTaskMutation,
    persistence: PersistenceBackend,
    versions: VersionTracker,
    timings: TaskTimingTracker,
) -> TaskMutationResult:
    """Delete a task.

    Args:
        mutation: Deletion request with task identifier.
        persistence: Backend for task storage.
        versions: Version tracker for optimistic concurrency.
        timings: Creation-time tracker (entry dropped on delete).

    Returns:
        Result with ``success=True`` on deletion, or a failure
        with ``error_code="not_found"`` if the task does not exist.
    """
    deleted = await persistence.tasks.delete(mutation.task_id)
    if not deleted:
        return _not_found_result("delete", mutation.request_id, mutation.task_id)

    versions.remove(mutation.task_id)
    timings.remove(mutation.task_id)

    logger.info(
        TASK_ENGINE_MUTATION_APPLIED,
        mutation_type="delete",
        request_id=mutation.request_id,
        task_id=mutation.task_id,
    )
    return TaskMutationResult(
        request_id=mutation.request_id,
        success=True,
        version=0,
    )


async def apply_cancel(
    mutation: CancelTaskMutation,
    persistence: PersistenceBackend,
    versions: VersionTracker,
    timings: TaskTimingTracker,
) -> TaskMutationResult:
    """Cancel a task (shortcut for transition to CANCELLED).

    Unlike :func:`apply_update` and :func:`apply_transition`, cancel
    intentionally omits an ``expected_version`` check -- a cancellation
    should always succeed regardless of version, similar to a forced
    stop signal.

    Args:
        mutation: Cancellation request with task identifier and reason.
        persistence: Backend for task storage.
        versions: Version tracker for optimistic concurrency.
        timings: Creation-time tracker; consulted to compute the
            duration observation for ``synthorg_task_runs_total`` /
            ``synthorg_task_duration_seconds``.

    Returns:
        Result with the cancelled task on success, or a failure with
        ``error_code`` of ``"not_found"`` or ``"validation"``.
    """
    task = await persistence.tasks.get(mutation.task_id)
    if task is None:
        return _not_found_result("cancel", mutation.request_id, mutation.task_id)

    previous_status = task.status
    try:
        updated = task.with_transition(TaskStatus.CANCELLED)
    except ValueError as exc:
        logger.warning(
            TASK_ENGINE_MUTATION_FAILED,
            mutation_type="cancel",
            request_id=mutation.request_id,
            task_id=mutation.task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=False,
            error=safe_error_description(exc),
            error_code="validation",
        )

    await persistence.tasks.save(updated)
    version = versions.bump(mutation.task_id)

    logger.info(
        TASK_ENGINE_MUTATION_APPLIED,
        mutation_type="cancel",
        request_id=mutation.request_id,
        task_id=mutation.task_id,
        from_status=previous_status.value,
        to_status=TaskStatus.CANCELLED.value,
        reason=mutation.reason,
    )
    logger.info(
        TASK_ENGINE_STATUS_TRANSITIONED,
        request_id=mutation.request_id,
        task_id=mutation.task_id,
        from_status=previous_status.value,
        to_status=TaskStatus.CANCELLED.value,
    )

    record_task_run(
        outcome="cancelled",
        duration_sec=_compute_task_duration_sec(
            timings,
            mutation.task_id,
            "cancel",
        ),
    )
    timings.remove(mutation.task_id)

    return TaskMutationResult(
        request_id=mutation.request_id,
        success=True,
        task=updated,
        version=version,
        previous_status=previous_status,
    )
