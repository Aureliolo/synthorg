"""Stateless helpers for TaskEngine mutation application.

Duration computation, validation-error formatting, and the
missing-task failure result. Each function takes explicit parameters
so the ``apply_*`` functions stay focused on the mutation flow.
"""

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import ValidationError as PydanticValidationError

from synthorg.core.clock import Clock
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.task_engine_models import TaskMutationResult
from synthorg.engine.task_engine_version import TaskTimingTracker
from synthorg.observability import get_logger
from synthorg.observability.events.task_engine import (
    TASK_ENGINE_MUTATION_FAILED,
    TASK_ENGINE_TIMING_FALLBACK,
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
# Statuses where the creation-timestamp entry can be safely dropped
# from ``TaskTimingTracker``. ``FAILED`` is excluded because the
# engine may retry a failed task; the retry's duration metric should
# still be measured from the original creation, not from "now -
# nothing" (which would degrade to the missing-timestamp WARN
# fallback every retry).
TRULY_TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.REJECTED},
)


def compute_task_duration_sec(
    timings: TaskTimingTracker,
    task_id: str,
    mutation_type: str,
    *,
    clock: Clock,
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

    Returns:
        Elapsed seconds since the task's tracked creation timestamp,
        clamped at ``0.0``; ``None`` when no timestamp is recorded.
    """
    created_at = timings.get_creation(task_id)
    if created_at is not None:
        return max(0.0, (clock.now() - created_at).total_seconds())
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
