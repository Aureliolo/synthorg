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

from synthorg.observability import get_logger
from synthorg.persistence._shared.datetime_marshaller import (
    format_iso_utc,
    parse_iso_utc,
)

__all__ = ("format_iso_utc", "normalize_utc", "parse_iso_utc")

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
