"""Shared helpers for SQLite and Postgres repository implementations.

The leading underscore signals "internal to the persistence layer".
Helpers expose pure functions that the backend repos call to remove
serialisation / deserialisation / error-classification duplication;
backend-specific bits (SQL placeholder style, JSON wrappers, error
class predicates) stay in the backend repo modules and are passed
into the helpers as callables.

Conformance tests target these helpers directly without instantiating
a database backend.
"""

from datetime import UTC, datetime
from typing import Final, overload

from synthorg.observability import get_logger
from synthorg.persistence._shared.datetime_marshaller import (
    canonical_deadline,
    coerce_row_timestamp,
    format_iso_utc,
    parse_iso_utc,
)
from synthorg.persistence._shared.pagination import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    collect_all,
    collect_all_mapping,
    paginate,
    validate_pagination_args,
)

__all__ = (
    "DEFAULT_LIST_LIMIT",
    "MAX_LIST_LIMIT",
    "TURN_APPEND_MAX_RETRIES",
    "canonical_deadline",
    "coerce_row_timestamp",
    "collect_all",
    "collect_all_mapping",
    "format_iso_utc",
    "normalize_utc",
    "paginate",
    "parse_iso_utc",
    "require_aware_utc",
    "safe_float",
    "safe_int",
    "sqlite_archive_timestamp",
    "validate_pagination_args",
)

logger = get_logger(__name__)

# Bounded retry on the (conversation_id, sequence) uniqueness race when
# appending a conversation turn. Two concurrent ``converse()`` calls can
# both compute the same sequence from a stale read; the loser re-queries
# the live max sequence and retries. Shared by both backends so the
# budget stays in lockstep. A caller losing repeatedly signals write-side
# contention worth surfacing as a constraint violation.
TURN_APPEND_MAX_RETRIES: Final[int] = 3


def normalize_utc(value: datetime) -> datetime:
    """Coerce a datetime to UTC-aware (naive treated as UTC).

    Single normalisation point shared by every persistence helper that
    round-trips timestamps. Naive datetimes are tagged as UTC (matches
    the project-wide rule "store UTC everywhere"); aware datetimes in
    other zones are converted via :py:meth:`~datetime.datetime.astimezone`.

    Args:
        value: Either tz-aware or naive datetime.

    Returns:
        UTC-aware datetime preserving the original instant.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def sqlite_archive_timestamp(value: datetime) -> object:
    """Render a UTC datetime the way SQLite stores a verdict ``recorded_at``.

    Shared by both SQLite verdict archives rather than copied into each:
    the two feed one keyset predicate, so a cursor timestamp rendered
    differently in each would compare against the TEXT column differently
    and page the two archives apart.

    Args:
        value: The timestamp to bind.

    Returns:
        The ISO-8601 UTC string the TEXT column compares against.
    """
    return format_iso_utc(normalize_utc(value))


def require_aware_utc(value: datetime, *, field: str) -> datetime:
    """Coerce to UTC, refusing a naive datetime outright.

    :func:`normalize_utc` reads a naive value AS UTC, which is right for a
    value a model already validated as aware and wrong for one a caller
    hands in raw: local wall-clock time read as UTC silently shifts the
    instant, and for a retention threshold that deletes a different set of
    rows than the one asked for.

    Args:
        value: The caller-supplied datetime.
        field: Parameter name, for the refusal message.

    Returns:
        The value in UTC.

    Raises:
        ValueError: When *value* carries no usable offset.
    """
    # Both halves, because a ``tzinfo`` whose ``utcoffset`` returns ``None``
    # is naive by the language's own definition, and ``astimezone`` reads it
    # as LOCAL time rather than refusing it: the exact silent instant-shift
    # this function exists to stop, arriving through the branch that looked
    # like it had already been checked.
    if value.tzinfo is None or value.utcoffset() is None:
        msg = (
            f"{field} must be timezone-aware; a naive datetime names no "
            "instant, so it cannot be compared against stored UTC."
        )
        raise ValueError(msg)
    return value.astimezone(UTC)


@overload
def safe_int(value: object, *, default: int = 0) -> int: ...
@overload
def safe_int(value: object, *, default: None) -> int | None: ...
def safe_int(value: object, *, default: int | None = 0) -> int | None:
    """Coerce a DB-row / config value to ``int``, falling back on failure.

    ``None`` and any value that ``int()`` rejects (non-numeric string,
    incompatible type) yield ``default``. Replaces the bare
    ``int(row[...] or 0)`` / ``int(x) if x is not None else None``
    idioms scattered across the SQLite and Postgres repositories so
    a malformed column never raises mid-deserialisation.

    Args:
        value: Raw value from a database row or config map.
        default: Returned when *value* is ``None`` or not int-coercible.

    Returns:
        The parsed integer, or *default*.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except TypeError, ValueError, OverflowError:
            return default
    return default


@overload
def safe_float(value: object, *, default: float = 0.0) -> float: ...
@overload
def safe_float(value: object, *, default: None) -> float | None: ...
def safe_float(value: object, *, default: float | None = 0.0) -> float | None:
    """Coerce a DB-row / config value to ``float``, falling back on failure.

    ``None`` and any value that ``float()`` rejects yield *default*.
    The float-specific sibling of :func:`safe_int`.

    Args:
        value: Raw value from a database row or config map.
        default: Returned when *value* is ``None`` or not float-coercible.

    Returns:
        The parsed float, or *default*.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except TypeError, ValueError, OverflowError:
            return default
    return default
