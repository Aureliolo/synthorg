"""ISO 8601 marshalling helpers for persistence repositories.

The strict round-tripping pair (:func:`parse_iso_utc` /
:func:`format_iso_utc`) is defined in :mod:`synthorg.core.iso_datetime`
and re-exported here so persistence repositories keep their existing
import surface. Naive datetimes are rejected: a naive value at this layer
is a programming bug, and the server's session time zone would otherwise
corrupt the instant.  For the relaxed "naive is UTC" semantics, use
:func:`synthorg.persistence._shared.normalize_utc`.

The :func:`coerce_row_timestamp` dispatcher accepts either flavour
(string or ``datetime``) and is the canonical helper for repository
``_row_to_*`` deserialisers, where the underlying driver may return
either type depending on connection configuration (SQLite TEXT vs
``detect_types``; Postgres ``TIMESTAMPTZ`` vs legacy ISO strings).
"""

from datetime import datetime

from synthorg.core.iso_datetime import format_iso_utc, parse_iso_utc


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

    Returns:
        Result of type ``datetime``.
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


def canonical_deadline(value: str | None) -> str | None:
    """Canonicalise a deadline string so both backends store one form.

    ``Project.deadline`` and ``Task.deadline`` are ISO 8601 STRINGS, not
    datetimes, and the model accepts every ISO shape a person might type: a
    UTC instant, an offset-bearing one, or a bare date. SQLite stores the text
    verbatim and hands it back unchanged; Postgres round-trips it through
    ``TIMESTAMPTZ`` and its reader formats the result as UTC. So the same
    project saved with ``2026-08-19T12:00:00+02:00`` reads back as itself on
    one backend and as ``2026-08-19T10:00:00Z`` on the other, and a
    conformance test comparing instants cannot see it.

    Applied on the WRITE path of both backends, so what is stored is what is
    read on either. A bare date has no instant of its own and is taken as
    midnight UTC, which is the reading Postgres already imposed.

    Args:
        value: The deadline as the model holds it, or ``None``.

    Returns:
        The canonical UTC ISO 8601 string, or ``None``.

    Raises:
        ValueError: If *value* is not parseable as ISO 8601. The model
            validates it first, so reaching this means the row was built
            around the model rather than through it.
    """
    if value is None:
        return None
    # Local import: ``normalize_utc`` lives in the package ``__init__``, which
    # imports from this module.
    from synthorg.persistence._shared import normalize_utc  # noqa: PLC0415

    return format_iso_utc(normalize_utc(datetime.fromisoformat(value)))


__all__ = (
    "canonical_deadline",
    "coerce_row_timestamp",
    "format_iso_utc",
    "parse_iso_utc",
)
