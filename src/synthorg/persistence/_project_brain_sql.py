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
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import cast

from synthorg.persistence._shared import coerce_row_timestamp
from synthorg.persistence.project_brain_protocol import BrainFilterSpec
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
)

BRAIN_COLUMNS = (
    "project_id, entry_id, revision, entry_kind, title, rationale, status, "
    "author, recorded_at, related_task_ids, related_entry_ids, "
    "supersedes_entry_id, tags, confidence, citations, payload"
)
"""Column list (insert/select order) shared by both backends."""


def _json_dumps_default(value: object) -> object:
    """Default JSON encoder: ``json.dumps`` for the SQLite TEXT columns.

    Object keys are sorted so the stored text is deterministic. Postgres
    injects a :class:`~psycopg.types.json.Jsonb` wrapper instead so the value
    lands in the native JSONB column, where ``sort_keys`` is unnecessary
    (JSONB stores a decomposed binary form and does not preserve key order).

    Returns:
        A JSON string.
    """
    return json.dumps(value, sort_keys=True)


def _like_array_contains(column: str, ph: str, value: str) -> tuple[str, object]:
    """Default array-membership predicate: substring ``LIKE`` on the JSON text.

    SQLite stores the array as a JSON string, so element membership is a
    quoted-substring match. Postgres injects a ``jsonb_exists`` predicate
    against the native JSONB array instead.

    The value is JSON-encoded before LIKE-escaping so the pattern matches
    the element exactly as ``json.dumps`` stored it -- quotes, backslashes,
    and non-ASCII characters are encoded identically on both sides, which a
    bare ``"<value>"`` wrapper would miss.

    Returns:
        ``(sql_fragment, bound_param)``.
    """
    return f"{column} LIKE {ph} ESCAPE '\\'", f"%{escape_like(json.dumps(value))}%"


def row_to_entry(
    row: Mapping[str, object],
    *,
    load_json: Callable[[object], object],
) -> BrainEntry:
    """Reconstruct a :class:`BrainEntry` from a DB row.

    Args:
        row: The row as a string-keyed mapping. Both repositories pass a
            ``dict`` (the SQLite repo wraps its ``aiosqlite.Row`` via
            ``dict(row)``; psycopg's ``dict_row`` is already a ``dict``).
        load_json: Backend JSON decoder (``json.loads`` for SQLite; a
            str-or-pre-parsed tolerant loader for Postgres).

    Returns:
        The reconstructed entry.
    """
    data = dict(row)
    data.pop("rn", None)
    data["entry_kind"] = BrainEntryKind(str(data["entry_kind"]))
    data["status"] = BrainEntryStatus(str(data["status"]))
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
    encode_json: Callable[[object], object] = _json_dumps_default,
) -> tuple[object, ...]:
    """Scalar SQL parameters for a full-row INSERT (revision included).

    Args:
        entity: The entry to persist.
        serialize_dt: Serialiser for ``recorded_at`` (ISO string for SQLite;
            passthrough for Postgres' native ``TIMESTAMPTZ``).
        encode_json: Serialiser for the JSON array / object columns
            (``json.dumps`` for SQLite's TEXT columns; a ``Jsonb`` wrapper for
            Postgres' native JSONB columns).

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
        encode_json(list(entity.related_task_ids)),
        encode_json(list(entity.related_entry_ids)),
        entity.supersedes_entry_id,
        encode_json(list(entity.tags)),
        entity.confidence,
        encode_json([c.model_dump(mode="json") for c in entity.citations]),
        encode_json(entity.payload.model_dump(mode="json")),
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
    array_contains: Callable[
        [str, str, str], tuple[str, object]
    ] = _like_array_contains,
) -> tuple[list[str], list[object]]:
    """Build the AND-conditions (kind/status/tag/author/task/since).

    Args:
        filter_spec: Filter dimensions.
        ph: Backend parameter placeholder (``?`` or ``%s``).
        serialize_dt: Serialiser for the ``updated_since`` timestamp.
        array_contains: Builds the array-membership predicate for the JSON
            array columns: substring ``LIKE`` on SQLite's TEXT, ``jsonb_exists``
            on Postgres' native JSONB.

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
        frag, param = array_contains("tags", ph, filter_spec.tag)
        conditions.append(frag)
        params.append(param)
    if filter_spec.related_task_id is not None:
        frag, param = array_contains(
            "related_task_ids", ph, filter_spec.related_task_id
        )
        conditions.append(frag)
        params.append(param)
    if filter_spec.updated_since is not None:
        conditions.append(f"recorded_at >= {ph}")
        params.append(serialize_dt(filter_spec.updated_since))
    return conditions, params


def build_filter_sql(
    filter_spec: BrainFilterSpec,
    *,
    ph: str,
    serialize_dt: Callable[[datetime], object],
    array_contains: Callable[
        [str, str, str], tuple[str, object]
    ] = _like_array_contains,
) -> tuple[str, tuple[object, ...]]:
    """Compose the ``WHERE`` clause for an all-revisions query.

    Returns:
        ``(where_sql, params)`` with ``project_id`` first.
    """
    conditions, params = filter_conditions(
        filter_spec, ph=ph, serialize_dt=serialize_dt, array_contains=array_contains
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
    array_contains: Callable[
        [str, str, str], tuple[str, object]
    ] = _like_array_contains,
) -> tuple[str, tuple[object, ...]]:
    """Compose the outer AND-fragment for current-state queries.

    Returns:
        ``(outer_and_sql, params)`` where ``outer_and_sql`` begins with `` AND``
        when non-empty.
    """
    conditions, params = filter_conditions(
        filter_spec, ph=ph, serialize_dt=serialize_dt, array_contains=array_contains
    )
    sql = (" AND " + " AND ".join(conditions)) if conditions else ""
    return sql, tuple(params)
