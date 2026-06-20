"""Shared serialisation helpers for ``EvolutionOutcomeRecord`` repos.

Both SQLite (``aiosqlite`` + TEXT/INTEGER columns) and Postgres
(``psycopg`` + TIMESTAMPTZ/BOOLEAN) write the same Pydantic
``EvolutionOutcomeRecord`` rows; the only differences are SQL
placeholder style, the boolean wrapper, and the timestamp wrapper. The
helpers below factor out the model-to-payload assembly and the
row-to-model deserialisation so the backend repos stay thin shims.

Conformance tests for ``EvolutionOutcomeRepository`` should target these
helpers directly so they exercise the canonical contract without
instantiating either backend.
"""

from collections.abc import Callable
from datetime import datetime

from pydantic import ValidationError

from synthorg.core.persistence_errors import MalformedRowError
from synthorg.core.types import NotBlankStr
from synthorg.meta.evolution.outcome_models import EvolutionOutcomeRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.evolution_outcome import (
    PERSISTENCE_EVOLUTION_OUTCOME_DESERIALIZE_FAILED,
)
from synthorg.persistence._shared import coerce_row_timestamp, normalize_utc

logger = get_logger(__name__)

# Column order is contract: both backends INSERT in this exact order.
OUTCOME_COLUMNS: tuple[str, ...] = (
    "agent_id",
    "axis",
    "applied",
    "proposed_at",
    "recorded_at",
)

TimestampSerializer = Callable[[datetime], object]
"""Serializes a UTC-normalised timestamp for the target driver.

SQLite passes ``format_iso_utc`` (TEXT column).
Postgres passes the identity (TIMESTAMPTZ accepts native ``datetime``).
"""

BoolSerializer = Callable[[bool], object]
"""Serializes the ``applied`` flag for the target driver.

SQLite passes ``int`` (INTEGER 0/1 column).
Postgres passes the identity (native BOOLEAN).
"""


def outcome_to_payload(
    record: EvolutionOutcomeRecord,
    *,
    timestamp_serializer: TimestampSerializer,
    bool_serializer: BoolSerializer,
) -> dict[str, object]:
    """Assemble the column-name -> value mapping for an INSERT.

    Both timestamps are normalised to UTC before serialisation so both
    backends store identical ordering keys regardless of the caller's
    tzinfo.

    Args:
        record: The outcome record to serialise.
        timestamp_serializer: Driver-specific datetime wrapper.
        bool_serializer: Driver-specific boolean wrapper for ``applied``.

    Returns:
        Dict keyed by :data:`OUTCOME_COLUMNS` for the INSERT.
    """
    return {
        "agent_id": str(record.agent_id),
        "axis": str(record.axis),
        "applied": bool_serializer(record.applied),
        "proposed_at": timestamp_serializer(normalize_utc(record.proposed_at)),
        "recorded_at": timestamp_serializer(normalize_utc(record.recorded_at)),
    }


def _require_str(value: object, field: str) -> str:
    """Return ``value`` when it is a string, else reject it.

    Rejecting a non-string outright avoids ``str(None) -> "None"`` smuggling
    a blank/None id past the ``NotBlankStr`` field validator. Blank strings
    are still caught by the model's ``NotBlankStr`` validation downstream.

    Returns:
        The value, narrowed to ``str``.

    Raises:
        TypeError: When ``value`` is not a string.
    """
    if not isinstance(value, str):
        msg = f"{field} must be a string, got {type(value).__name__}"
        raise TypeError(msg)
    return value


def _coerce_applied(value: object) -> bool:
    """Coerce the ``applied`` column to a strict bool.

    Accepts only a real boolean or the SQLite 0/1 INTEGER encoding; anything
    else (e.g. a truthy non-bool) is corrupt persisted data.

    Returns:
        The coerced boolean.

    Raises:
        ValueError: When ``value`` is neither a bool nor 0/1.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    msg = f"applied must be a bool or 0/1, got {value!r}"
    raise ValueError(msg)


def row_to_outcome_record(row: dict[str, object]) -> EvolutionOutcomeRecord:
    """Deserialise a row mapping into an :class:`EvolutionOutcomeRecord`.

    Tolerates SQLite's INTEGER ``applied`` (0/1) and TEXT timestamps as
    well as Postgres's native BOOLEAN / TIMESTAMPTZ.

    Args:
        row: Mapping from column name to raw driver value.

    Returns:
        The reconstructed record.

    Raises:
        MalformedRowError: If the row cannot be parsed or fails Pydantic
            validation. Non-retryable (data corruption is deterministic).
    """
    try:
        return EvolutionOutcomeRecord(
            agent_id=NotBlankStr(_require_str(row["agent_id"], "agent_id")),
            axis=NotBlankStr(_require_str(row["axis"], "axis")),
            applied=_coerce_applied(row["applied"]),
            proposed_at=coerce_row_timestamp(row["proposed_at"]),
            recorded_at=coerce_row_timestamp(row["recorded_at"]),
        )
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        agent = row.get("agent_id", "<unknown>")
        msg = f"Failed to deserialize evolution outcome for {agent!r}"
        logger.warning(
            PERSISTENCE_EVOLUTION_OUTCOME_DESERIALIZE_FAILED,
            agent_id=agent,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise MalformedRowError(msg) from exc
