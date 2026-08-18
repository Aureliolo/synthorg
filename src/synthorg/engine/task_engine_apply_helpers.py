"""Stateless helpers for TaskEngine mutation application.

Duration computation, validation-error formatting, and the
missing-task failure result. Each function takes explicit parameters
so the ``apply_*`` functions stay focused on the mutation flow.
"""

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType

from pydantic import ValidationError as PydanticValidationError

from synthorg.core.clock import Clock
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.observability import get_logger
from synthorg.observability.events.task_engine import (
    TASK_ENGINE_MUTATION_FAILED,
)

logger = get_logger(__name__)

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
# Use ``TRULY_TERMINAL_STATUSES`` below when you need the latter
# (e.g. for deciding whether to clear the timing tracker).
RECORDED_STATUS_OUTCOME: Mapping[TaskStatus, str] = MappingProxyType(
    {
        TaskStatus.COMPLETED: "succeeded",
        TaskStatus.FAILED: "failed",
        TaskStatus.CANCELLED: "cancelled",
        TaskStatus.REJECTED: "rejected",
    },
)
# Statuses that mean the task is done forever, as opposed to the
# recorded-outcome map above. ``FAILED`` is excluded because the engine may
# retry a failed task.
TRULY_TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.REJECTED},
)


def compute_task_duration_sec(
    created_at: datetime,
    *,
    clock: Clock,
) -> float:
    """Return ``now - created_at`` in seconds, clamped at zero.

    The baseline is the task row's own creation time, so this always has an
    answer: a restart cannot lose it and a retry still measures from the
    original creation rather than from the point it was retried. Both
    properties are why the value is a persisted column and not something the
    process holds: the duration of a task is asked about precisely when a
    process did not see it start.

    Returns:
        Elapsed seconds since the task was filed, clamped at ``0.0`` so a
        clock adjustment cannot produce a negative observation.
    """
    return max(0.0, (clock.now() - created_at).total_seconds())


def format_validation_error(
    prefix: str,
    exc: PydanticValidationError,
) -> str:
    """Format a Pydantic validation error for external consumption.

    Extracts field paths and messages without exposing raw input
    values or internal Pydantic URL hints.

    Returns:
        A ``"{prefix}: field.path: msg; field.path: msg"`` string
        suitable for surfacing in API error responses.
    """
    parts = [
        f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()
    ]
    return f"{prefix}: {'; '.join(parts)}"


def not_found_result(
    mutation_type: str,
    request_id: str,
    task_id: str,
) -> TaskMutationResult:
    """Build a failure result for a missing task and log it.

    Sets ``error_code='not_found'`` on the result.

    Returns:
        A :class:`TaskMutationResult` with ``success=False`` and
        ``error_code="not_found"``.
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
