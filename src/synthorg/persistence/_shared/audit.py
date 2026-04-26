"""Shared serialisation/deserialisation helpers for ``AuditEntry`` repos.

Both SQLite (``aiosqlite`` + TEXT columns) and Postgres (``psycopg`` +
TIMESTAMPTZ + JSONB) write the same Pydantic ``AuditEntry`` rows; the
only differences are SQL placeholder style, JSON wrapper, and the
exception class raised on a duplicate-id INSERT. The helpers below
factor out the model-to-payload assembly, the row-to-model
deserialisation, and the duplicate-vs-other error classification, so
the backend repos shrink to thin shims that pass the right
driver-specific callable into each helper.

Conformance tests for ``AuditRepository`` should target these helpers
directly so they exercise the canonical contract without instantiating
either backend.
"""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from pydantic import ValidationError

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_AUDIT_ENTRY_DESERIALIZE_FAILED,
    PERSISTENCE_AUDIT_ENTRY_SAVE_FAILED,
)
from synthorg.persistence._shared import coerce_row_timestamp, normalize_utc
from synthorg.persistence.errors import (
    DuplicateRecordError,
    MalformedRowError,
    QueryError,
)
from synthorg.security.models import AuditEntry

logger = get_logger(__name__)

# Column order is contract: both backends INSERT in this exact order.
AUDIT_COLUMNS: tuple[str, ...] = (
    "id",
    "timestamp",
    "agent_id",
    "task_id",
    "tool_name",
    "tool_category",
    "action_type",
    "arguments_hash",
    "verdict",
    "risk_level",
    "reason",
    "matched_rules",
    "evaluation_duration_ms",
    "approval_id",
)


JsonSerializer = Callable[[list[str]], Any]
"""Serializes the ``matched_rules`` list for the target driver.

SQLite passes ``json.dumps`` (TEXT column).
Postgres passes ``psycopg.types.json.Jsonb`` (JSONB column).
"""

TimestampSerializer = Callable[[datetime], Any]
"""Serializes the UTC-normalised timestamp for the target driver.

SQLite passes ``lambda dt: dt.isoformat()`` (TEXT column).
Postgres passes the identity (TIMESTAMPTZ accepts native ``datetime``).
"""


def audit_entry_to_payload(
    entry: AuditEntry,
    *,
    json_serializer: JsonSerializer,
    timestamp_serializer: TimestampSerializer,
) -> dict[str, Any]:
    """Assemble the column-name -> value mapping for an INSERT.

    Timestamp is normalised to UTC before being passed through the
    driver's timestamp serializer, so both backends store identical
    ordering keys regardless of the caller's tzinfo.

    Args:
        entry: The audit entry to serialise.
        json_serializer: Driver-specific JSON wrapper for
            ``matched_rules``.
        timestamp_serializer: Driver-specific datetime wrapper for
            ``timestamp``.

    Returns:
        Dict keyed by ``AUDIT_COLUMNS`` containing the values to bind
        into the INSERT statement.
    """
    data = entry.model_dump(mode="json")
    utc_ts = normalize_utc(entry.timestamp)
    return {
        **data,
        "timestamp": timestamp_serializer(utc_ts),
        "matched_rules": json_serializer(list(data["matched_rules"])),
    }


def row_to_audit_entry(row: dict[str, object]) -> AuditEntry:
    """Deserialise a row mapping into an :class:`AuditEntry`.

    Tolerates both string-encoded ``matched_rules`` (SQLite stores
    JSON as TEXT) and natively-decoded list (Postgres JSONB returns
    Python list/dict directly).

    Args:
        row: Mapping from column name to raw driver value.

    Raises:
        MalformedRowError: If the row cannot be parsed or fails Pydantic
            validation. Non-retryable (data corruption is deterministic;
            retrying re-reads the same bad row). The original exception
            is logged via ``safe_error_description`` (no payload bytes
            leak through traceback frame-locals).
    """
    try:
        raw_rules = row.get("matched_rules")
        parsed: dict[str, object]
        if isinstance(raw_rules, str):
            parsed = {**row, "matched_rules": json.loads(raw_rules)}
        else:
            parsed = dict(row)
        # Normalise timestamp to UTC before validation so SQLite (TEXT
        # ISO strings) and Postgres (TIMESTAMPTZ) round-trip to the
        # same instant. ``AwareDatetime`` only enforces tz-awareness,
        # not UTC, so without this step a Postgres column with a
        # non-UTC offset would survive validation but compare unequal
        # to the SQLite read in conformance tests.
        ts = parsed.get("timestamp")
        if ts is not None:
            parsed["timestamp"] = coerce_row_timestamp(ts)
        return AuditEntry.model_validate(parsed)
    except (
        ValidationError,
        json.JSONDecodeError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        # ValueError covers ``datetime.fromisoformat()`` failures on
        # malformed persisted timestamps so they take the same
        # structured-log path as JSON / validation errors.
        row_id = row.get("id", "<unknown>")
        msg = f"Failed to deserialize audit entry {row_id!r}"
        logger.warning(
            PERSISTENCE_AUDIT_ENTRY_DESERIALIZE_FAILED,
            entry_id=row_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MalformedRowError(msg) from exc


class IsDuplicate(Protocol):
    """Driver-specific predicate that classifies an INSERT failure.

    SQLite checks for ``"UNIQUE constraint failed: audit_entries.id"``
    or ``"PRIMARY KEY"`` substrings in ``str(exc)``.
    Postgres checks ``isinstance(exc, psycopg.errors.UniqueViolation)``.
    """

    def __call__(self, exc: BaseException) -> bool: ...


def classify_audit_save_error(
    exc: BaseException,
    *,
    entry_id: str,
    is_duplicate: IsDuplicate,
) -> DuplicateRecordError | QueryError:
    """Convert a driver INSERT exception into the canonical persistence error.

    Logs the failure with structured fields (``error_type``,
    ``error=safe_error_description(exc)``, ``duplicate=<bool>``) so
    audit log records never embed traceback frame-locals or unredacted
    ``str(exc)`` payloads.

    Args:
        exc: The original driver exception.
        entry_id: The ID of the entry whose INSERT failed (for log
            context).
        is_duplicate: Driver-specific predicate that returns ``True``
            for a duplicate-key violation.

    Returns:
        A :class:`DuplicateRecordError` when ``is_duplicate(exc)``,
        otherwise a :class:`QueryError`. The caller is expected to
        ``raise <returned> from exc``.
    """
    duplicate = is_duplicate(exc)
    if duplicate:
        msg = f"Duplicate audit entry {entry_id!r}"
        logger.warning(
            PERSISTENCE_AUDIT_ENTRY_SAVE_FAILED,
            entry_id=entry_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            duplicate=True,
        )
        return DuplicateRecordError(msg)
    msg = f"Failed to save audit entry {entry_id!r}"
    logger.warning(
        PERSISTENCE_AUDIT_ENTRY_SAVE_FAILED,
        entry_id=entry_id,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
        duplicate=False,
    )
    return QueryError(msg)
