"""Backend-agnostic row <-> model marshalling for project charters.

The SQLite and Postgres charter repositories deserialise the same
``project_charters`` columns into the same :class:`ProjectCharter`
model and flatten it back into the same positional upsert params. The
row objects differ (``aiosqlite.Row`` vs psycopg ``dict_row``) but both
support string-key indexing, so this module's :class:`RowLike` marshaller
serves both backends; the timestamp coercer normalises ``TEXT`` /
``TIMESTAMPTZ`` alike, and the ``forecast_id`` branch tolerates both a
native :class:`~uuid.UUID` (Postgres) and a string (SQLite).
"""

import json
from datetime import datetime
from typing import LiteralString
from uuid import UUID

from synthorg.core.persistence_errors import MalformedRowError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.enums import CharterStatus
from synthorg.meta.charter.models import (
    BudgetEnvelope,
    ProjectCharter,
    ScopeBoundaries,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.charter import PERSISTENCE_CHARTER_FAILED
from synthorg.persistence._shared.datetime_marshaller import (
    coerce_row_timestamp,
    format_iso_utc,
)
from synthorg.persistence._shared.rows import RowLike
from synthorg.persistence.charter_protocol import CharterFilterSpec

logger = get_logger(__name__)

CHARTER_COLUMNS: LiteralString = (
    "id, conversation_id, created_by, version, status, title, brief, "
    "goals, constraints, success_criteria, in_scope, out_of_scope, "
    "envelope_amount, envelope_currency, envelope_deadline, "
    "envelope_time_horizon, project_id, proposed_project_name, "
    "proposed_project_description, created_at, updated_at, approved_at, "
    "approved_by, forecast_id, correlation_id, task_id"
)

_ALLOWED_TRANSITION_KEYS = frozenset(
    {
        "updated_at",
        "approved_at",
        "approved_by",
        "forecast_id",
        "correlation_id",
        "task_id",
    }
)


def _decode_str_tuple(raw: object) -> tuple[NotBlankStr, ...]:
    """Decode a JSON array column into a tuple of non-blank strings.

    Returns:
        The matching collection.
    """
    if raw is None:
        return ()
    decoded = json.loads(str(raw))
    return tuple(NotBlankStr(str(item)) for item in decoded)


def _encode_str_tuple(values: tuple[str, ...]) -> str:
    """Encode a string tuple as a deterministic JSON array.

    Returns:
        Result of type ``str``.
    """
    return json.dumps(list(values))


def as_iso(value: object) -> str | None:
    """Normalise a timestamp update value to an ISO-8601 UTC string.

    Returns:
        The matching value, or ``None`` when absent.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return format_iso_utc(value)
    return str(value)


def row_to_charter(row: RowLike) -> ProjectCharter:
    """Convert a database row into a :class:`ProjectCharter`.

    Returns:
        Result of type ``ProjectCharter``.

    Raises:
        MalformedRowError: If the row contains corrupt or unparseable data.
    """
    try:
        deadline_raw = row["envelope_deadline"]
        approved_at_raw = row["approved_at"]
        forecast_raw = row["forecast_id"]
        envelope = BudgetEnvelope(
            amount=float(str(row["envelope_amount"])),
            currency=str(row["envelope_currency"]),
            deadline=(
                coerce_row_timestamp(deadline_raw) if deadline_raw is not None else None
            ),
            time_horizon=(
                str(row["envelope_time_horizon"])
                if row["envelope_time_horizon"] is not None
                else None
            ),
        )
        scope = ScopeBoundaries(
            in_scope=_decode_str_tuple(row["in_scope"]),
            out_of_scope=_decode_str_tuple(row["out_of_scope"]),
        )
        return ProjectCharter(
            id=NotBlankStr(str(row["id"])),
            conversation_id=NotBlankStr(str(row["conversation_id"])),
            created_by=NotBlankStr(str(row["created_by"])),
            version=int(str(row["version"])),
            status=CharterStatus(str(row["status"])),
            title=NotBlankStr(str(row["title"])),
            brief=NotBlankStr(str(row["brief"])),
            goals=_decode_str_tuple(row["goals"]),
            constraints=_decode_str_tuple(row["constraints"]),
            success_criteria=_decode_str_tuple(row["success_criteria"]),
            scope=scope,
            envelope=envelope,
            project_id=(
                NotBlankStr(str(row["project_id"]))
                if row["project_id"] is not None
                else None
            ),
            proposed_project_name=(
                NotBlankStr(str(row["proposed_project_name"]))
                if row["proposed_project_name"] is not None
                else None
            ),
            proposed_project_description=str(row["proposed_project_description"]),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
            approved_at=(
                coerce_row_timestamp(approved_at_raw)
                if approved_at_raw is not None
                else None
            ),
            approved_by=(
                NotBlankStr(str(row["approved_by"]))
                if row["approved_by"] is not None
                else None
            ),
            forecast_id=(
                forecast_raw
                if isinstance(forecast_raw, UUID)
                else (UUID(str(forecast_raw)) if forecast_raw is not None else None)
            ),
            correlation_id=(
                NotBlankStr(str(row["correlation_id"]))
                if row["correlation_id"] is not None
                else None
            ),
            task_id=(
                NotBlankStr(str(row["task_id"])) if row["task_id"] is not None else None
            ),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = (
            f"Failed to parse project charter row: "
            f"{type(exc).__name__} ({safe_error_description(exc)})"
        )
        logger.warning(
            PERSISTENCE_CHARTER_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MalformedRowError(msg) from exc


def charter_save_params(entity: ProjectCharter) -> tuple[object, ...]:
    """Flatten a charter into the positional upsert params.

    Returns:
        The matching collection.
    """
    return (
        entity.id,
        entity.conversation_id,
        entity.created_by,
        int(entity.version),
        entity.status.value,
        entity.title,
        entity.brief,
        _encode_str_tuple(entity.goals),
        _encode_str_tuple(entity.constraints),
        _encode_str_tuple(entity.success_criteria),
        _encode_str_tuple(entity.scope.in_scope),
        _encode_str_tuple(entity.scope.out_of_scope),
        float(entity.envelope.amount),
        entity.envelope.currency,
        (
            format_iso_utc(entity.envelope.deadline)
            if entity.envelope.deadline is not None
            else None
        ),
        entity.envelope.time_horizon,
        entity.project_id,
        entity.proposed_project_name,
        entity.proposed_project_description,
        format_iso_utc(entity.created_at),
        format_iso_utc(entity.updated_at),
        (
            format_iso_utc(entity.approved_at)
            if entity.approved_at is not None
            else None
        ),
        entity.approved_by,
        (str(entity.forecast_id) if entity.forecast_id is not None else None),
        entity.correlation_id,
        entity.task_id,
    )


def build_charter_where(
    filter_spec: CharterFilterSpec, *, placeholder: LiteralString
) -> tuple[LiteralString, list[object]]:
    """Build the WHERE clause + bound params from a filter spec.

    Args:
        filter_spec: The charter filter predicates.
        placeholder: The backend's bound-parameter token (``?`` / ``%s``).

    Returns:
        ``(where_clause, params)``: SQL fragment + positional params.
    """
    clauses: list[LiteralString] = []
    params: list[object] = []
    if filter_spec.status is not None:
        clauses.append(f"status = {placeholder}")
        params.append(filter_spec.status.value)
    if filter_spec.project_id is not None:
        clauses.append(f"project_id = {placeholder}")
        params.append(filter_spec.project_id)
    if filter_spec.created_by is not None:
        clauses.append(f"created_by = {placeholder}")
        params.append(filter_spec.created_by)
    if filter_spec.conversation_id is not None:
        clauses.append(f"conversation_id = {placeholder}")
        params.append(filter_spec.conversation_id)
    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


def _cas_update_sql(placeholder: LiteralString) -> LiteralString:
    """Assemble the conditional-edit UPDATE for one placeholder token.

    Built from the ``CHARTER_COLUMNS`` LiteralString (minus ``id``) so the
    SET list cannot drift from the upsert column set; the f-string stays
    ``LiteralString`` because every interpolated part is itself a
    compile-time constant.

    Returns:
        The full ``UPDATE ... SET ... WHERE`` statement.
    """
    assignments: LiteralString = (
        f"conversation_id = {placeholder}, created_by = {placeholder}, "
        f"version = {placeholder}, status = {placeholder}, "
        f"title = {placeholder}, brief = {placeholder}, "
        f"goals = {placeholder}, constraints = {placeholder}, "
        f"success_criteria = {placeholder}, in_scope = {placeholder}, "
        f"out_of_scope = {placeholder}, envelope_amount = {placeholder}, "
        f"envelope_currency = {placeholder}, envelope_deadline = {placeholder}, "
        f"envelope_time_horizon = {placeholder}, project_id = {placeholder}, "
        f"proposed_project_name = {placeholder}, "
        f"proposed_project_description = {placeholder}, "
        f"created_at = {placeholder}, updated_at = {placeholder}, "
        f"approved_at = {placeholder}, approved_by = {placeholder}, "
        f"forecast_id = {placeholder}, correlation_id = {placeholder}, "
        f"task_id = {placeholder}"
    )
    return (
        f"UPDATE project_charters SET {assignments} "  # noqa: S608 -- constants only
        f"WHERE id = {placeholder} AND version = {placeholder} "
        f"AND status = {placeholder}"
    )


CHARTER_CAS_UPDATE_SQL_QMARK: LiteralString = _cas_update_sql("?")
"""Version+DRAFTED-guarded conditional-edit UPDATE (SQLite ``?`` token)."""

CHARTER_CAS_UPDATE_SQL_PCT: LiteralString = _cas_update_sql("%s")
"""Version+DRAFTED-guarded conditional-edit UPDATE (Postgres ``%s`` token).

A concurrent edit (version moved) or a concurrent approve / cancel
(status moved) leaves the row unmatched, so the UPDATE affects zero rows
and the caller surfaces a conflict instead of silently clobbering the
other writer (lost-update invariant; bespoke under ADR-0001 D7).
"""


def charter_cas_params(
    entity: ProjectCharter, *, expected_version: int
) -> tuple[object, ...]:
    """Positional params for :func:`charter_cas_update_sql`.

    Returns:
        The SET params (all non-``id`` columns, from ``charter_save_params``)
        followed by the WHERE params ``(id, expected_version, DRAFTED)``.
    """
    set_params = charter_save_params(entity)[1:]
    return (
        *set_params,
        entity.id,
        int(expected_version),
        CharterStatus.DRAFTED.value,
    )


def validate_charter_update_keys(updates: dict[str, object]) -> None:
    """Reject unknown ``transition_if`` update keys.

    Raises:
        QueryError: If the caller passed unsupported update keys.
    """
    unknown = sorted(set(updates) - _ALLOWED_TRANSITION_KEYS)
    if unknown:
        msg = f"transition_if rejects unknown update keys: {unknown!r}"
        logger.warning(PERSISTENCE_CHARTER_FAILED, operation="transition_if", error=msg)
        raise QueryError(msg)


__all__ = [
    "CHARTER_CAS_UPDATE_SQL_PCT",
    "CHARTER_CAS_UPDATE_SQL_QMARK",
    "CHARTER_COLUMNS",
    "as_iso",
    "build_charter_where",
    "charter_cas_params",
    "charter_save_params",
    "row_to_charter",
    "validate_charter_update_keys",
]
