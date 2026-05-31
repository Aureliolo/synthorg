"""Backend-agnostic SQL helpers shared by the project-brain repositories.

The SQLite and Postgres repositories build identical column lists, filter
clauses, INSERT parameter tuples, and row->model reconstruction. They differ only
in the parameter placeholder (``?`` vs ``%s``), how they serialise the
``recorded_at`` / ``updated_since`` timestamps, and how they decode JSON columns.
All three differences are injected here (placeholder string, datetime serialiser,
JSON loader), so the shared logic lives in one place and each repository keeps
only its connection handling.
"""

import json
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, cast

from psycopg.rows import DictRow

from synthorg.persistence._shared import coerce_row_timestamp
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
)

if TYPE_CHECKING:
    from synthorg.persistence.project_brain_protocol import BrainFilterSpec

BRAIN_COLUMNS = (
    "project_id, entry_id, revision, entry_kind, title, rationale, status, "
    "author, recorded_at, related_task_ids, related_entry_ids, "
    "supersedes_entry_id, tags, confidence, citations, payload"
)
"""Column list (insert/select order) shared by both backends."""


def row_to_entry(
    row: DictRow,
    *,
    load_json: Callable[[str], object],
) -> BrainEntry:
    """Reconstruct a :class:`BrainEntry` from a DB row.

    Args:
        row: The row mapping (``aiosqlite.Row`` or psycopg ``dict_row``).
        load_json: Backend JSON decoder (``json.loads`` for SQLite; a
            str-or-pre-parsed tolerant loader for Postgres).

    Returns:
        The reconstructed entry.
    """
    data = dict(row)
    data.pop("rn", None)
    data["entry_kind"] = BrainEntryKind(data["entry_kind"])
    data["status"] = BrainEntryStatus(data["status"])
    data["tags"] = tuple(cast("list[object]", load_json(data["tags"])))
    data["related_task_ids"] = tuple(
        cast("list[object]", load_json(data["related_task_ids"]))
    )
    data["related_entry_ids"] = tuple(
        cast("list[object]", load_json(data["related_entry_ids"]))
    )
    data["citations"] = load_json(data["citations"])
    data["payload"] = load_json(data["payload"])
    data["recorded_at"] = coerce_row_timestamp(data["recorded_at"])
    return BrainEntry.model_validate(data)


def insert_params(
    entity: BrainEntry,
    *,
    serialize_dt: Callable[[datetime], object],
) -> tuple[object, ...]:
    """Scalar SQL parameters for a full-row INSERT (revision included).

    Args:
        entity: The entry to persist.
        serialize_dt: Serialiser for ``recorded_at`` (ISO string for SQLite;
            passthrough for Postgres' native ``TIMESTAMPTZ``).

    Returns:
        The positional parameter tuple in column order.
    """
    return (
        entity.project_id,
        entity.entry_id,
        entity.revision,
        entity.entry_kind.value,
        entity.title,
        entity.rationale,
        entity.status.value,
        entity.author,
        serialize_dt(entity.recorded_at),
        json.dumps(list(entity.related_task_ids)),
        json.dumps(list(entity.related_entry_ids)),
        entity.supersedes_entry_id,
        json.dumps(list(entity.tags), sort_keys=True),
        entity.confidence,
        json.dumps([c.model_dump(mode="json") for c in entity.citations]),
        json.dumps(entity.payload.model_dump(mode="json"), sort_keys=True),
    )


def escape_like(value: str) -> str:
    r"""Escape LIKE metacharacters so a JSON needle matches literally.

    Returns:
        The escaped value (paired with ``ESCAPE '\'`` in the query).
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def filter_conditions(
    filter_spec: BrainFilterSpec,
    *,
    ph: str,
    serialize_dt: Callable[[datetime], object],
) -> tuple[list[str], list[object]]:
    """Build the AND-conditions (kind/status/tag/author/task/since).

    Args:
        filter_spec: Filter dimensions.
        ph: Backend parameter placeholder (``?`` or ``%s``).
        serialize_dt: Serialiser for the ``updated_since`` timestamp.

    Returns:
        ``(conditions, params)`` excluding the leading ``project_id`` predicate.
    """
    conditions: list[str] = []
    params: list[object] = []
    if filter_spec.entry_kind is not None:
        conditions.append(f"entry_kind = {ph}")
        params.append(filter_spec.entry_kind.value)
    if filter_spec.status is not None:
        conditions.append(f"status = {ph}")
        params.append(filter_spec.status.value)
    if filter_spec.author is not None:
        conditions.append(f"author = {ph}")
        params.append(filter_spec.author)
    if filter_spec.tag is not None:
        conditions.append(f"tags LIKE {ph} ESCAPE '\\'")
        params.append(f'%"{escape_like(filter_spec.tag)}"%')
    if filter_spec.related_task_id is not None:
        conditions.append(f"related_task_ids LIKE {ph} ESCAPE '\\'")
        params.append(f'%"{escape_like(filter_spec.related_task_id)}"%')
    if filter_spec.updated_since is not None:
        conditions.append(f"recorded_at >= {ph}")
        params.append(serialize_dt(filter_spec.updated_since))
    return conditions, params


def build_filter_sql(
    filter_spec: BrainFilterSpec,
    *,
    ph: str,
    serialize_dt: Callable[[datetime], object],
) -> tuple[str, tuple[object, ...]]:
    """Compose the ``WHERE`` clause for an all-revisions query.

    Returns:
        ``(where_sql, params)`` with ``project_id`` first.
    """
    conditions, params = filter_conditions(
        filter_spec, ph=ph, serialize_dt=serialize_dt
    )
    sql = f"WHERE project_id = {ph}"
    full_params: list[object] = [filter_spec.project_id, *params]
    if conditions:
        sql += " AND " + " AND ".join(conditions)
    return sql, tuple(full_params)


def build_current_filter_sql(
    filter_spec: BrainFilterSpec,
    *,
    ph: str,
    serialize_dt: Callable[[datetime], object],
) -> tuple[str, tuple[object, ...]]:
    """Compose the outer AND-fragment for current-state queries.

    Returns:
        ``(outer_and_sql, params)`` where ``outer_and_sql`` begins with `` AND``
        when non-empty.
    """
    conditions, params = filter_conditions(
        filter_spec, ph=ph, serialize_dt=serialize_dt
    )
    sql = (" AND " + " AND ".join(conditions)) if conditions else ""
    return sql, tuple(params)
