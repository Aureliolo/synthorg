"""Shared serialisation helpers for ``Alert`` repos.

Both SQLite (``aiosqlite`` + TEXT/JSON-as-TEXT columns) and Postgres
(``psycopg`` + TIMESTAMPTZ/JSONB) write the same Pydantic ``Alert``
rows; the only differences are SQL placeholder style and the JSON /
timestamp wrapper. The helpers below factor out the model-to-payload
assembly and the row-to-model deserialisation so the backend repos
stay thin shims.

Conformance tests for ``AlertRepository`` should target these helpers
directly so they exercise the canonical contract without instantiating
either backend.
"""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from synthorg.core.persistence_errors import MalformedRowError
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import Alert
from synthorg.meta.models import RuleSeverity
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.alert import (
    PERSISTENCE_ALERT_DESERIALIZE_FAILED,
)
from synthorg.persistence._shared import coerce_row_timestamp

logger = get_logger(__name__)

# Column order is contract: both backends INSERT in this exact order.
ALERT_COLUMNS: tuple[str, ...] = (
    "id",
    "severity",
    "alert_type",
    "description",
    "affected_domains",
    "signal_context",
    "recommended_action",
    "emitted_at",
)

TimestampSerializer = Callable[[datetime], object]
"""Serializes a UTC-normalised timestamp for the target driver.

SQLite passes ``format_iso_utc`` (TEXT column).
Postgres passes the identity (TIMESTAMPTZ accepts native ``datetime``).
"""

JsonSerializer = Callable[[object], object]
"""Serializes a JSON-shaped value for the target driver.

SQLite passes ``json.dumps`` (TEXT column).
Postgres passes ``psycopg.types.json.Jsonb`` (native JSONB column).
"""


def alert_to_payload(
    alert: Alert,
    *,
    timestamp_serializer: TimestampSerializer,
    json_serializer: JsonSerializer,
) -> dict[str, object]:
    """Assemble the column-name -> value mapping for an INSERT.

    Args:
        alert: The alert to serialise.
        timestamp_serializer: Driver-specific datetime wrapper.
        json_serializer: Driver-specific JSON wrapper for the array /
            object columns.

    Returns:
        Dict keyed by :data:`ALERT_COLUMNS` for the INSERT.
    """
    return {
        "id": str(alert.id),
        "severity": alert.severity.value,
        "alert_type": alert.alert_type,
        "description": str(alert.description),
        "affected_domains": json_serializer(list(alert.affected_domains)),
        "signal_context": json_serializer(dict(alert.signal_context)),
        "recommended_action": (
            str(alert.recommended_action)
            if alert.recommended_action is not None
            else None
        ),
        "emitted_at": timestamp_serializer(alert.emitted_at),
    }


def _require_str(value: object, field: str) -> str:
    """Return ``value`` when it is a string, else reject it.

    Returns:
        The value, narrowed to ``str``.

    Raises:
        TypeError: When ``value`` is not a string.
    """
    if not isinstance(value, str):
        msg = f"{field} must be a string, got {type(value).__name__}"
        raise TypeError(msg)
    return value


def _load_json_array(value: object) -> list[object]:
    """Decode a JSON array column value from either driver's native form.

    SQLite returns the TEXT column as a ``str``; Postgres's ``dict_row``
    cursor already decodes JSONB into a native ``list``.

    Returns:
        The decoded JSON array.

    Raises:
        TypeError: When the decoded value is not a list.
    """
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, list):
        msg = f"expected a JSON array, got {type(decoded).__name__}"
        raise TypeError(msg)
    return decoded


def _load_json_object(value: object) -> dict[str, object]:
    """Decode a JSON object column value from either driver's native form.

    SQLite returns the TEXT column as a ``str``; Postgres's ``dict_row``
    cursor already decodes JSONB into a native ``dict``.

    Returns:
        The decoded JSON object.

    Raises:
        TypeError: When the decoded value is not a dict with string keys.
    """
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict) or not all(isinstance(k, str) for k in decoded):
        msg = f"expected a JSON object, got {type(decoded).__name__}"
        raise TypeError(msg)
    return decoded


def row_to_alert(row: dict[str, object]) -> Alert:
    """Deserialise a row mapping into an :class:`Alert`.

    Args:
        row: Mapping from column name to raw driver value.

    Returns:
        The reconstructed alert.

    Raises:
        MalformedRowError: If the row cannot be parsed or fails Pydantic
            validation. Non-retryable (data corruption is deterministic).
    """
    alert_id = row.get("id", "<unknown>")
    try:
        alert_type_raw = _require_str(row["alert_type"], "alert_type")
        recommended_action = row.get("recommended_action")
        return Alert(
            id=UUID(_require_str(row["id"], "id")),
            severity=RuleSeverity(_require_str(row["severity"], "severity")),
            alert_type=_coerce_alert_type(alert_type_raw),
            description=NotBlankStr(_require_str(row["description"], "description")),
            affected_domains=tuple(
                NotBlankStr(str(d)) for d in _load_json_array(row["affected_domains"])
            ),
            signal_context=_load_json_object(row["signal_context"]),
            recommended_action=(
                NotBlankStr(str(recommended_action))
                if recommended_action is not None
                else None
            ),
            emitted_at=coerce_row_timestamp(row["emitted_at"]),
        )
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        msg = f"Failed to deserialize alert for {alert_id!r}"
        logger.warning(
            PERSISTENCE_ALERT_DESERIALIZE_FAILED,
            alert_id=alert_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MalformedRowError(msg) from exc


def _coerce_alert_type(
    value: str,
) -> Literal["inflection", "threshold", "trend"]:
    """Narrow a raw column string to the ``Alert.alert_type`` literal.

    Returns:
        The narrowed literal value.

    Raises:
        ValueError: When ``value`` is not one of the three known types.
    """
    if value in ("inflection", "threshold", "trend"):
        return value  # type: ignore[return-value]
    msg = f"alert_type must be one of inflection/threshold/trend, got {value!r}"
    raise ValueError(msg)
