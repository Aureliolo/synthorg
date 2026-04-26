"""ISO 8601 marshalling helpers for persistence repositories.

Strict pair for round-tripping timestamps through ISO 8601 strings
(SQLite TEXT columns, JSON envelopes, settings ``updated_at``).  Naive
datetimes are rejected: a naive value at this layer is a programming
bug, and the server's session time zone would otherwise corrupt the
instant.  For the relaxed "naive is UTC" semantics, use
:func:`synthorg.persistence._shared.normalize_utc`.

The :func:`coerce_row_timestamp` dispatcher accepts either flavour
(string or ``datetime``) and is the canonical helper for repository
``_row_to_*`` deserialisers, where the underlying driver may return
either type depending on connection configuration (SQLite TEXT vs
``detect_types``; Postgres ``TIMESTAMPTZ`` vs legacy ISO strings).
"""

from datetime import UTC, datetime


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


def coerce_row_timestamp(value: object) -> datetime:
    """Coerce a row timestamp value (``str`` or ``datetime``) to UTC.

    Repository ``_row_to_*`` deserialisers receive timestamps that
    may arrive in either shape:

    * **SQLite TEXT** columns return ``str`` by default, but a
      connection configured with ``detect_types=PARSE_DECLTYPES``
      (or a registered converter) hands back ``datetime``.
    * **Postgres TIMESTAMPTZ** columns return tz-aware ``datetime``
      via psycopg, but the offset reflects the session timezone, so
      a non-UTC ``SET TIME ZONE`` would otherwise leak into the
      Pydantic model and break cross-backend conformance.
    * **Legacy / migrated rows** in either backend may persist as
      ISO 8601 strings even where the column is now typed.

    Strings parse via the strict :func:`parse_iso_utc` (naive ISO
    strings raise ``ValueError``); ``datetime`` values normalize via
    :func:`synthorg.persistence._shared.normalize_utc` (treats naive
    as UTC, calls ``astimezone(UTC)`` on aware).  Any other type
    raises ``TypeError`` so a corrupt row surfaces loudly via the
    enclosing ``MalformedRowError`` / ``QueryError`` path rather than
    silently producing garbage.

    Raises:
        ValueError: If ``value`` is a string that does not parse as a
            timezone-aware ISO 8601 datetime.
        TypeError: If ``value`` is neither ``str`` nor ``datetime``.
    """
    if isinstance(value, datetime):
        # Local import keeps the marshaller module dependency-free at
        # import time -- ``normalize_utc`` lives in the package
        # ``__init__`` which itself imports from this module.
        from synthorg.persistence._shared import normalize_utc  # noqa: PLC0415

        return normalize_utc(value)
    if isinstance(value, str):
        return parse_iso_utc(value)
    msg = f"Unsupported timestamp type {type(value).__name__}"
    raise TypeError(msg)


__all__ = ("coerce_row_timestamp", "format_iso_utc", "parse_iso_utc")
