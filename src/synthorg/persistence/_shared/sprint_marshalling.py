"""Backend-agnostic row <-> model marshalling for agile sprints.

The SQLite and Postgres sprint repositories deserialise the same
``sprints`` columns into the same :class:`Sprint` model and flatten it
back into the same positional upsert params. The row objects differ
(``aiosqlite.Row`` vs psycopg ``dict_row``) but both support string-key
indexing, so this module's :class:`RowLike` marshaller serves both
backends.

``task_ids`` / ``completed_task_ids`` are stored as JSON arrays and
``task_points`` as a JSON object (TEXT on SQLite, native JSONB on
Postgres). ``start_date`` / ``end_date`` are the domain model's own
ISO-8601 strings (the ``Sprint`` model types them as ``str | None``, not
``datetime``), so they are persisted verbatim as nullable TEXT on both
backends for a lossless round-trip.
"""

import json
from collections.abc import Callable, Mapping
from typing import LiteralString, NoReturn

from synthorg.core.persistence_errors import (
    ConstraintViolationError,
    MalformedRowError,
    QueryError,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import (
    OPEN_SPRINT_STATUS_VALUES,
    Sprint,
    SprintStatus,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.sprint import PERSISTENCE_SPRINT_FAILED
from synthorg.persistence._shared.rows import RowLike
from synthorg.persistence.sprint_protocol import SprintFilterSpec

logger = get_logger(__name__)

SPRINT_COLUMNS: LiteralString = (
    "id, project, name, goal, status, sprint_number, duration_days, "
    "start_date, end_date, task_ids, completed_task_ids, task_points, "
    "story_points_committed, story_points_completed"
)

_ALLOWED_TRANSITION_KEYS = frozenset({"start_date", "end_date"})


def _decode_str_tuple(raw: object) -> tuple[NotBlankStr, ...]:
    """Decode a JSON array column into a tuple of non-blank strings.

    Tolerates both backends: SQLite returns a JSON ``str`` (needs parsing);
    Postgres' native JSONB column returns an already-parsed ``list``.

    Returns:
        The matching collection.

    Raises:
        TypeError: If the decoded value is not a JSON array.
    """
    if raw is None:
        return ()
    items = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(items, list):
        msg = f"expected a JSON array, got {type(items).__name__}"
        raise TypeError(msg)
    return tuple(NotBlankStr(str(item)) for item in items)


def encode_str_tuple(values: tuple[str, ...]) -> object:
    """Encode a string tuple as a deterministic JSON array (SQLite default).

    The Postgres sprint repository injects a ``Jsonb`` encoder instead so the
    value binds to the native JSONB column.

    Returns:
        A JSON string.
    """
    return json.dumps(list(values))


def _decode_float_map(raw: object) -> dict[str, float]:
    """Decode a JSON object column into a ``{task_id: points}`` mapping.

    Tolerates both backends: SQLite returns a JSON ``str`` (needs parsing);
    Postgres' native JSONB column returns an already-parsed ``dict``.

    Returns:
        The decoded per-task points mapping.

    Raises:
        TypeError: If the decoded value is not a JSON object.
    """
    if raw is None:
        return {}
    items = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(items, dict):
        msg = f"expected a JSON object, got {type(items).__name__}"
        raise TypeError(msg)
    return {str(key): float(value) for key, value in items.items()}


def encode_float_map(values: Mapping[str, float]) -> object:
    """Encode a ``{task_id: points}`` mapping as a JSON object (SQLite default).

    The Postgres sprint repository injects a ``Jsonb`` encoder instead so the
    value binds to the native JSONB column.

    Returns:
        A JSON string.
    """
    return json.dumps(dict(values))


def row_to_sprint(row: RowLike) -> Sprint:
    """Convert a database row into a :class:`Sprint`.

    Returns:
        Result of type ``Sprint``.

    Raises:
        MalformedRowError: If the row contains corrupt or unparseable data.
    """
    try:
        project_raw = row["project"]
        start_raw = row["start_date"]
        end_raw = row["end_date"]
        return Sprint(
            id=NotBlankStr(str(row["id"])),
            project=(
                NotBlankStr(str(project_raw)) if project_raw is not None else None
            ),
            name=NotBlankStr(str(row["name"])),
            goal=str(row["goal"]),
            status=SprintStatus(str(row["status"])),
            sprint_number=int(str(row["sprint_number"])),
            duration_days=int(str(row["duration_days"])),
            start_date=(str(start_raw) if start_raw is not None else None),
            end_date=(str(end_raw) if end_raw is not None else None),
            task_ids=_decode_str_tuple(row["task_ids"]),
            completed_task_ids=_decode_str_tuple(row["completed_task_ids"]),
            task_points=_decode_float_map(row["task_points"]),
            story_points_committed=float(str(row["story_points_committed"])),
            story_points_completed=float(str(row["story_points_completed"])),
        )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        # aiosqlite/sqlite3 Row raises IndexError (not KeyError) on a
        # missing column, so both are caught for cross-backend robustness.
        msg = (
            f"Failed to parse sprint row: "
            f"{type(exc).__name__} ({safe_error_description(exc)})"
        )
        logger.warning(
            PERSISTENCE_SPRINT_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MalformedRowError(msg) from exc


def sprint_save_params(
    entity: Sprint,
    *,
    encode_array: Callable[[tuple[str, ...]], object] = encode_str_tuple,
    encode_map: Callable[[Mapping[str, float]], object] = encode_float_map,
) -> tuple[object, ...]:
    """Flatten a sprint into the positional upsert params.

    Args:
        entity: The sprint to flatten.
        encode_array: Serialiser for the JSON-array columns (``json.dumps``
            for SQLite's TEXT columns; a ``Jsonb`` wrapper for Postgres' native
            JSONB columns).
        encode_map: Serialiser for the ``task_points`` JSON-object column
            (same SQLite/Postgres split as ``encode_array``).

    Returns:
        The matching collection, ordered to match :data:`SPRINT_COLUMNS`.
    """
    return (
        entity.id,
        entity.project,
        entity.name,
        entity.goal,
        entity.status.value,
        int(entity.sprint_number),
        int(entity.duration_days),
        entity.start_date,
        entity.end_date,
        encode_array(entity.task_ids),
        encode_array(entity.completed_task_ids),
        encode_map(entity.task_points),
        float(entity.story_points_committed),
        float(entity.story_points_completed),
    )


def build_sprint_where(
    filter_spec: SprintFilterSpec, *, placeholder: LiteralString
) -> tuple[LiteralString, list[object]]:
    """Build the WHERE clause + bound params from a filter spec.

    Args:
        filter_spec: The sprint filter predicates.
        placeholder: The backend's bound-parameter token (``?`` / ``%s``).

    Returns:
        ``(where_clause, params)``: SQL fragment + positional params.
    """
    clauses: list[LiteralString] = []
    params: list[object] = []
    if filter_spec.project is not None:
        clauses.append(f"project = {placeholder}")
        params.append(filter_spec.project)
    if filter_spec.org_wide_only:
        # The org-wide scope is a value the column carries, not the
        # absence of a predicate: an unset ``project`` means "every
        # scope", so without this clause there is no way to ask for the
        # sprints that belong to no project.
        clauses.append("project IS NULL")
    if filter_spec.status is not None:
        clauses.append(f"status = {placeholder}")
        params.append(filter_spec.status.value)
    if filter_spec.after is not None:
        # A row-value comparison rather than the expanded
        # ``a < ? OR (a = ? AND b < ?)``: it is the same predicate, and
        # both backends index it, but written out the two forms are one
        # transcription error apart and the error is invisible (it drops
        # or duplicates rows only at a page boundary). ``<`` because
        # ``ORDER_BY`` is descending on both columns, so "after" in
        # iteration order is "below" in value order.
        clauses.append(f"(sprint_number, id) < ({placeholder}, {placeholder})")
        params.extend([filter_spec.after.sprint_number, filter_spec.after.sprint_id])
    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


def open_status_placeholders(placeholder: LiteralString) -> LiteralString:
    """Render one bound-parameter slot per admissible completion status.

    Derived from :data:`OPEN_SPRINT_STATUS_VALUES` rather than written
    out, because the values are already derived from it: a hand-written
    ``IN (?, ?)`` beside a starred parameter tuple silently binds the
    wrong number of slots the moment a third status becomes admissible.

    Args:
        placeholder: The backend's bound-parameter token (``?`` / ``%s``).

    Returns:
        The comma-separated slot list for an ``IN`` predicate.
    """
    return ", ".join(placeholder for _ in OPEN_SPRINT_STATUS_VALUES)


def complete_task_params(*, sprint_id: str, task_id: str) -> tuple[object, ...]:
    """Positional params for the guarded completion statement.

    Shared so both backends bind the open statuses from
    :data:`OPEN_SPRINT_STATUS_VALUES` rather than inlining them: a status
    literal written into the SQL is a second answer to "when may a task be
    completed", and it drifts from the enum the service reads the first
    time the lifecycle changes.

    Args:
        sprint_id: The sprint whose backlog is being marked.
        task_id: The delivered task; bound four times (appended, named as
            the point-bearing key the re-derived total must now include,
            then checked present in the backlog and absent from the
            completed set).

    Returns:
        The params, ordered to match both backends' statement.
    """
    return (
        task_id,
        task_id,
        sprint_id,
        *OPEN_SPRINT_STATUS_VALUES,
        task_id,
        task_id,
    )


def add_task_params(
    *, sprint_id: str, task_id: str, story_points: float, max_tasks: int
) -> tuple[object, ...]:
    """Positional params for the guarded backlog-assembly statement.

    Args:
        sprint_id: The sprint whose backlog is being assembled.
        task_id: The task to add; bound as the appended array element,
            twice as the ``task_points`` key (once to write it, once
            inside the subquery that re-totals the merged mapping), and
            once more for the not-already-present guard.
        story_points: What this task commits, bound alongside each of the
            two ``task_points`` writes.
        max_tasks: The backlog cap the statement holds against the row's
            own current length.

    Returns:
        The params, ordered to match both backends' statement.
    """
    points = float(story_points)
    return (
        task_id,
        task_id,
        points,
        task_id,
        points,
        sprint_id,
        SprintStatus.PLANNING.value,
        task_id,
        max_tasks,
    )


def raise_existing_sprint(sprint_id: str) -> NoReturn:
    """Refuse a ``save`` aimed at a sprint that is already persisted.

    Shared so both backends refuse in the same words: the row count is
    what each of them observes, but what the refusal MEANS is one fact
    about the sprint contract, and two hand-written messages are two
    chances to describe it differently.

    Args:
        sprint_id: The row the insert conflicted with.

    Raises:
        ConstraintViolationError: Always; the caller reached this only
            because a row already carries *sprint_id*.
    """
    msg = (
        f"sprint {sprint_id!r} already exists; save creates a sprint and "
        "every later change goes through transition_if, add_task_if_planning "
        "or complete_task_if, each of which holds its guard against the "
        "row's current value"
    )
    logger.warning(PERSISTENCE_SPRINT_FAILED, operation="save", sprint_id=sprint_id)
    raise ConstraintViolationError(msg, constraint="sprints.id")


def validate_sprint_update_keys(updates: dict[str, object]) -> None:
    """Reject unknown ``transition_if`` update keys.

    Raises:
        QueryError: If the caller passed unsupported update keys.
    """
    unknown = sorted(set(updates) - _ALLOWED_TRANSITION_KEYS)
    if unknown:
        msg = f"transition_if rejects unknown update keys: {unknown!r}"
        logger.warning(PERSISTENCE_SPRINT_FAILED, operation="transition_if", error=msg)
        raise QueryError(msg)


__all__ = [
    "SPRINT_COLUMNS",
    "add_task_params",
    "build_sprint_where",
    "complete_task_params",
    "encode_float_map",
    "encode_str_tuple",
    "open_status_placeholders",
    "raise_existing_sprint",
    "row_to_sprint",
    "sprint_save_params",
    "validate_sprint_update_keys",
]
