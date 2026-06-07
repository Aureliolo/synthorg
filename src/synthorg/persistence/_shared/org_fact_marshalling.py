"""Backend-agnostic marshalling for organisational facts (MVCC).

The SQLite and Postgres org-fact repositories deserialise the same
``org_facts_snapshot`` / ``org_facts_operation_log`` rows into the same
domain models. Tags are stored as a JSON array (TEXT on SQLite, JSONB on
Postgres), so :func:`tags_from_json` accepts a JSON string or an
already-decoded list. Timestamps (TEXT ISO vs ``TIMESTAMPTZ``) are
normalised by :func:`coerce_row_timestamp`. The per-backend MVCC SQL
(notably the divergent ``snapshot_at`` query) stays in the backend
modules; only the row <-> model marshalling lives here.
"""

import json
from typing import Literal, cast

from pydantic import ValidationError

from synthorg.core.enums import AutonomyLevel, OrgFactCategory
from synthorg.core.types import NotBlankStr
from synthorg.hr.seniority import SeniorityLevel
from synthorg.memory.org.errors import OrgMemoryQueryError
from synthorg.memory.org.models import (
    OperationLogEntry,
    OperationLogSnapshot,
    OrgFact,
    OrgFactAuthor,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.org_memory import ORG_MEMORY_ROW_PARSE_FAILED
from synthorg.persistence._shared.datetime_marshaller import coerce_row_timestamp
from synthorg.persistence._shared.rows import RowLike

logger = get_logger(__name__)

_OperationType = Literal["PUBLISH", "RETRACT"]
_ROW_PARSE_ERRORS = (
    KeyError,
    ValueError,
    TypeError,
    ValidationError,
    OrgMemoryQueryError,
)


def _opt_str(value: object) -> str | None:
    """Return ``str(value)`` or ``None`` when *value* is falsy/``None``."""
    return None if value is None else str(value)


def tags_to_json(tags: tuple[NotBlankStr, ...]) -> str:
    """Serialise a tags tuple to a sorted JSON array.

    Returns:
        Result of type ``str``.
    """
    return json.dumps(sorted(tags))


def tags_from_json(raw: object) -> tuple[NotBlankStr, ...]:
    """Deserialise tags (JSON string or JSONB-decoded list) to a tuple.

    Returns:
        The matching collection.

    Raises:
        OrgMemoryQueryError: If the column is not a JSON array of
            non-blank strings.
    """
    parsed = raw if isinstance(raw, list) else json.loads(str(raw))
    if not isinstance(parsed, list):
        msg = f"Tags must be a JSON array, got {type(parsed).__name__}"
        logger.warning(ORG_MEMORY_ROW_PARSE_FAILED, error=msg)
        raise OrgMemoryQueryError(msg)
    if any(not isinstance(t, str) or not t.strip() for t in parsed):
        msg = "Tags must be a JSON array of non-blank strings"
        logger.warning(ORG_MEMORY_ROW_PARSE_FAILED, error=msg)
        raise OrgMemoryQueryError(msg)
    return tuple(NotBlankStr(cast("str", t)) for t in parsed)


def _author_from_row(row: RowLike) -> OrgFactAuthor:
    """Reconstruct an :class:`OrgFactAuthor` from a row's author columns.

    Returns:
        Result of type ``OrgFactAuthor``.
    """
    seniority = row["author_seniority"]
    autonomy = row["author_autonomy_level"]
    return OrgFactAuthor(
        agent_id=_opt_str(row["author_agent_id"]),
        seniority=SeniorityLevel(str(seniority)) if seniority else None,
        autonomy_level=AutonomyLevel(str(autonomy)) if autonomy else None,
        is_human=bool(row["author_is_human"]),
    )


def snapshot_row_to_org_fact(row: RowLike) -> OrgFact:
    """Reconstruct an ``OrgFact`` from a snapshot row.

    Returns:
        Result of type ``OrgFact``.

    Raises:
        OrgMemoryQueryError: If the row cannot be deserialised.
    """
    try:
        return OrgFact(
            id=str(row["fact_id"]),
            content=str(row["content"]),
            category=OrgFactCategory(str(row["category"])),
            tags=tags_from_json(row["tags"]),
            author=_author_from_row(row),
            created_at=coerce_row_timestamp(row["created_at"]),
        )
    except _ROW_PARSE_ERRORS as exc:
        logger.warning(
            ORG_MEMORY_ROW_PARSE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to deserialize snapshot row: {safe_error_description(exc)}"
        raise OrgMemoryQueryError(msg) from exc


def row_to_operation_log_entry(row: RowLike) -> OperationLogEntry:
    """Reconstruct an ``OperationLogEntry`` from a database row.

    Returns:
        Result of type ``OperationLogEntry``.

    Raises:
        OrgMemoryQueryError: If the row cannot be deserialised.
    """
    try:
        category = row["category"]
        seniority = row["author_seniority"]
        autonomy = row["author_autonomy_level"]
        return OperationLogEntry(
            operation_id=str(row["operation_id"]),
            fact_id=str(row["fact_id"]),
            operation_type=cast("_OperationType", str(row["operation_type"])),
            content=_opt_str(row["content"]),
            category=OrgFactCategory(str(category)) if category else None,
            tags=tags_from_json(row["tags"]),
            author_agent_id=_opt_str(row["author_agent_id"]),
            author_seniority=SeniorityLevel(str(seniority)) if seniority else None,
            author_is_human=bool(row["author_is_human"]),
            author_autonomy_level=AutonomyLevel(str(autonomy)) if autonomy else None,
            timestamp=coerce_row_timestamp(row["timestamp"]),
            version=int(str(row["version"])),
        )
    except _ROW_PARSE_ERRORS as exc:
        logger.warning(
            ORG_MEMORY_ROW_PARSE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to deserialize operation log row: {safe_error_description(exc)}"
        raise OrgMemoryQueryError(msg) from exc


def row_to_snapshot(row: RowLike) -> OperationLogSnapshot:
    """Reconstruct an ``OperationLogSnapshot`` from a time-travel query row.

    Returns:
        Result of type ``OperationLogSnapshot``.

    Raises:
        OrgMemoryQueryError: If the row cannot be deserialised.
    """
    try:
        op_type = str(row["operation_type"])
        retracted_at = (
            coerce_row_timestamp(row["timestamp"]) if op_type == "RETRACT" else None
        )
        created_at_raw = row["created_at"]
        created_at = coerce_row_timestamp(
            created_at_raw if created_at_raw is not None else row["timestamp"]
        )
        return OperationLogSnapshot(
            fact_id=str(row["fact_id"]),
            content=str(row["content"]),
            category=OrgFactCategory(str(row["category"])),
            tags=tags_from_json(row["tags"]),
            created_at=created_at,
            retracted_at=retracted_at,
            version=int(str(row["version"])),
        )
    except _ROW_PARSE_ERRORS as exc:
        logger.warning(
            ORG_MEMORY_ROW_PARSE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to deserialize snapshot_at row: {safe_error_description(exc)}"
        raise OrgMemoryQueryError(msg) from exc


__all__ = [
    "row_to_operation_log_entry",
    "row_to_snapshot",
    "snapshot_row_to_org_fact",
    "tags_from_json",
    "tags_to_json",
]
