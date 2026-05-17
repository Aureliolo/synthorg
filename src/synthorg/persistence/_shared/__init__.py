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
from typing import overload

from synthorg.observability import get_logger
from synthorg.persistence._shared.datetime_marshaller import (
    coerce_row_timestamp,
    format_iso_utc,
    parse_iso_utc,
)
from synthorg.persistence._shared.pagination import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    paginate,
    validate_pagination_args,
)

__all__ = (
    "DEFAULT_LIST_LIMIT",
    "MAX_LIST_LIMIT",
    "coerce_row_timestamp",
    "format_iso_utc",
    "normalize_utc",
    "paginate",
    "parse_iso_utc",
    "safe_float",
    "safe_int",
    "validate_pagination_args",
)

logger = get_logger(__name__)


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
        except TypeError, ValueError:
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
        except TypeError, ValueError:
            return default
    return default
