"""Postgres-native binding adapters for the project-brain repository.

Thin wrappers that bind the backend-agnostic ``_project_brain_sql`` builders
to psycopg's native types: ``datetime`` passes through unchanged (psycopg binds
it directly), JSON columns bind via :class:`~psycopg.types.json.Jsonb`, and
array-membership predicates use ``jsonb_exists`` against native JSONB arrays.
Kept out of ``project_brain_repo`` so the repository module stays focused on
the query methods rather than the marshalling glue.
"""

import json
from datetime import datetime

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from synthorg.persistence._project_brain_sql import (
    build_current_filter_sql,
    build_filter_sql,
    insert_params,
    row_to_entry,
)
from synthorg.persistence.project_brain_protocol import BrainFilterSpec
from synthorg.project_brain.models import BrainEntry


def load_json(value: object) -> object:
    """Parse a JSON column that Postgres may return as ``str`` or pre-parsed.

    Returns:
        The decoded JSON value.
    """
    if isinstance(value, str):
        return json.loads(value)
    return value


def row_to_brain_entry(row: DictRow) -> BrainEntry:
    """Reconstruct a :class:`BrainEntry` from a Postgres ``dict_row``.

    Returns:
        The reconstructed entry.
    """
    return row_to_entry(row, load_json=load_json)


def _passthrough_dt(value: datetime) -> object:
    """Return *value* unchanged: psycopg binds ``datetime`` natively.

    Returns:
        The datetime, for the shared serialiser slots.
    """
    return value


def _encode_jsonb(value: object) -> object:
    """Wrap a JSON value for binding to a native JSONB column.

    Returns:
        A :class:`~psycopg.types.json.Jsonb` adapter.
    """
    return Jsonb(value)


def _jsonb_exists_contains(column: str, ph: str, value: str) -> tuple[str, object]:
    """Array-membership predicate against a native JSONB array.

    Returns:
        ``(sql_fragment, bound_param)`` using ``jsonb_exists`` (the function
        form of the ``?`` operator, which psycopg binds without escaping).
    """
    return f"jsonb_exists({column}, {ph})", value


def brain_insert_params(entity: BrainEntry) -> tuple[object, ...]:
    """Positional INSERT parameters (``recorded_at`` + JSON bound natively).

    Returns:
        The positional parameter tuple in column order.
    """
    return insert_params(
        entity, serialize_dt=_passthrough_dt, encode_json=_encode_jsonb
    )


def brain_filter_sql(filter_spec: BrainFilterSpec) -> tuple[str, tuple[object, ...]]:
    """All-revisions ``WHERE`` clause for this backend (``%s`` placeholders).

    Returns:
        ``(where_sql, params)`` with ``project_id`` first.
    """
    return build_filter_sql(
        filter_spec,
        ph="%s",
        serialize_dt=_passthrough_dt,
        array_contains=_jsonb_exists_contains,
    )


def brain_current_filter_sql(
    filter_spec: BrainFilterSpec,
) -> tuple[str, tuple[object, ...]]:
    """Outer AND-fragment for current-state queries (``%s`` placeholders).

    Returns:
        ``(outer_and_sql, params)`` beginning with `` AND`` when non-empty.
    """
    return build_current_filter_sql(
        filter_spec,
        ph="%s",
        serialize_dt=_passthrough_dt,
        array_contains=_jsonb_exists_contains,
    )
