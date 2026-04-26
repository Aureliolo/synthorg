"""ISO 8601 marshalling helpers for persistence repositories.

Strict pair for round-tripping timestamps through ISO 8601 strings
(SQLite TEXT columns, JSON envelopes, settings ``updated_at``).  Naive
datetimes are rejected: a naive value at this layer is a programming
bug, and the server's session time zone would otherwise corrupt the
instant.  For the relaxed "naive is UTC" semantics, use
:func:`synthorg.persistence._shared.normalize_utc`.
"""

from datetime import UTC, datetime

__all__ = ("format_iso_utc", "parse_iso_utc")


def parse_iso_utc(value: str) -> datetime:
    """Parse an ISO 8601 string to a tz-aware UTC datetime.

    Args:
        value: An ISO 8601 string with explicit timezone information --
            either a UTC offset (e.g. ``+00:00``, ``-05:00``), a named
            timezone, or the ``Z`` suffix (Zulu, equivalent to
            ``+00:00``).  Naive timestamps are rejected.

    Returns:
        A tz-aware datetime normalized to UTC via
        :py:meth:`~datetime.datetime.astimezone`.

    Raises:
        ValueError: If ``value`` is not a parseable ISO 8601 string,
            or parses to a naive datetime.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        msg = f"timestamp must be timezone-aware, got naive value {value!r}"
        raise ValueError(msg)
    return parsed.astimezone(UTC)


def format_iso_utc(value: datetime) -> str:
    """Format a tz-aware datetime as a UTC ISO 8601 string.

    Args:
        value: A tz-aware datetime.

    Returns:
        ISO 8601 string with a ``+00:00`` offset suffix.

    Raises:
        ValueError: If ``value`` is naive (``tzinfo`` is ``None``).
    """
    if value.tzinfo is None:
        msg = f"timestamp must be timezone-aware, got naive datetime {value!r}"
        raise ValueError(msg)
    return value.astimezone(UTC).isoformat()
