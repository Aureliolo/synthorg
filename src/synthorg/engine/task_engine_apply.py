"""Mutation application logic for TaskEngine.

Each ``apply_*`` function takes the mutation, a persistence backend,
and a :class:`VersionTracker`, returning a :class:`TaskMutationResult`.
Extracted from ``task_engine.py`` to keep the main module focused on
lifecycle, queue management, and the public API.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

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
from synthorg.observability import get_logger
from synthorg.observability.events.task_engine import (
    TASK_ENGINE_MUTATION_APPLIED,
    TASK_ENGINE_MUTATION_FAILED,
)

if TYPE_CHECKING:
    from synthorg.engine.task_engine_version import VersionTracker
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
) -> TaskMutationResult:
    """Dispatch and apply a mutation by type.

    Raises:
        TypeError: If the mutation type is unrecognised.
    """
    match mutation:
        case CreateTaskMutation():
            return await apply_create(mutation, persistence, versions)
        case UpdateTaskMutation():
            return await apply_update(mutation, persistence, versions)
        case TransitionTaskMutation():
            return await apply_transition(mutation, persistence, versions)
        case DeleteTaskMutation():
            return await apply_delete(mutation, persistence, versions)
        case CancelTaskMutation():
            return await apply_cancel(mutation, persistence, versions)
        case _:
            msg = f"Unknown mutation type: {type(mutation).__name__}"  # type: ignore[unreachable]
            raise TypeError(msg)


# ── Apply methods ────────────────────────────────────────────────


async def apply_create(
    mutation: CreateTaskMutation,
    persistence: PersistenceBackend,
    versions: VersionTracker,
) -> TaskMutationResult:
    """Create a new task.

    Args:
        mutation: Creation request with task data.
        persistence: Backend for task storage.
        versions: Version tracker for optimistic concurrency.

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
            error=str(exc),
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
) -> TaskMutationResult:
    """Perform a task status transition.

    Args:
        mutation: Transition request with target status and reason.
        persistence: Backend for task storage.
        versions: Version tracker for optimistic concurrency.

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
            error=str(exc),
            error_code="version_conflict",
        )

    previous_status = task.status

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
            error=str(exc),
        )
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=False,
            error=str(exc),
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
) -> TaskMutationResult:
    """Delete a task.

    Args:
        mutation: Deletion request with task identifier.
        persistence: Backend for task storage.
        versions: Version tracker for optimistic concurrency.

    Returns:
        Result with ``success=True`` on deletion, or a failure
        with ``error_code="not_found"`` if the task does not exist.
    """
    deleted = await persistence.tasks.delete(mutation.task_id)
    if not deleted:
        return _not_found_result("delete", mutation.request_id, mutation.task_id)

    versions.remove(mutation.task_id)

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
) -> TaskMutationResult:
    """Cancel a task (shortcut for transition to CANCELLED).

    Unlike :func:`apply_update` and :func:`apply_transition`, cancel
    intentionally omits an ``expected_version`` check — a cancellation
    should always succeed regardless of version, similar to a forced
    stop signal.

    Args:
        mutation: Cancellation request with task identifier and reason.
        persistence: Backend for task storage.
        versions: Version tracker for optimistic concurrency.

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
            error=str(exc),
        )
        return TaskMutationResult(
            request_id=mutation.request_id,
            success=False,
            error=str(exc),
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
    return TaskMutationResult(
        request_id=mutation.request_id,
        success=True,
        task=updated,
        version=version,
        previous_status=previous_status,
    )
