"""ISO 8601 datetime parsers and the strict UTC marshalling pair.

This is the foundation home for the timestamp helpers shared across
layers. Four shapes live here so each pattern is written once:

* :func:`parse_iso_utc` / :func:`format_iso_utc` -- the strict pair for
  round-tripping timestamps through ISO 8601 strings (SQLite TEXT
  columns, JSON envelopes, settings ``updated_at``, and any domain code
  that crosses a UTC-only boundary). Naive datetimes are rejected: a
  naive value at these boundaries is a programming bug, and a server
  session time zone would otherwise corrupt the instant. The persistence
  layer re-exports these from ``persistence/_shared/datetime_marshaller``.
* :func:`parse_iso_assume_utc` -- for externally-authored timestamps that
  may legitimately omit an offset (Mem0 records, OAuth provider
  metadata): a naive value is assumed UTC rather than rejected, and an
  already-aware value is preserved as-is.
* :func:`parse_git_log_timestamp` -- for ``git log`` author/commit dates,
  which always carry an explicit offset: a naive value signals a
  malformed row and is rejected (``None``), and the original offset is
  preserved.

The two relaxed parsers keep an already-aware datetime exactly as parsed
(no UTC re-normalisation) so callers that store the value keep the source
offset.
"""

from datetime import UTC, datetime


def parse_iso_utc(value: str) -> datetime:
    """Parse an ISO 8601 string to a tz-aware UTC datetime.

    Delegates to :py:meth:`datetime.datetime.fromisoformat`, which
    accepts only numeric UTC offsets (e.g. ``+00:00``, ``-05:00``,
    ``+01:30``) or the ``Z`` suffix (Zulu, equivalent to ``+00:00``).
    IANA timezone names like ``Europe/Zurich`` or ``UTC`` are *not*
    accepted by ``fromisoformat`` and will raise ``ValueError``;
    callers that need to ingest such input must convert to an offset
    representation first (e.g. via ``ZoneInfo`` + ``isoformat()``).

    Args:
        value: An ISO 8601 string with explicit timezone information,
            either a numeric UTC offset (``+00:00``, ``-05:00``, ...) or
            the ``Z`` suffix.  Naive timestamps are rejected.

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


def parse_iso_assume_utc(raw: str) -> datetime:
    """Parse an ISO 8601 string, assuming UTC when no offset is present.

    Args:
        raw: An ISO 8601 datetime string.

    Returns:
        A tz-aware datetime. A naive value is stamped with UTC; an
        already-aware value is returned unchanged.

    Raises:
        ValueError: If ``raw`` is not a parseable ISO 8601 string.
        TypeError: If ``raw`` is not a string.
    """
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def parse_git_log_timestamp(raw: str) -> datetime | None:
    """Parse a ``git log`` ISO 8601 timestamp, rejecting naive values.

    ``git log`` author/commit dates carry an explicit offset, so a naive
    value indicates a malformed row and is rejected (``None``) rather
    than silently assumed UTC. The original offset is preserved.

    Args:
        raw: The ISO 8601 timestamp field from a ``git log`` row.

    Returns:
        The aware datetime (original offset preserved), or ``None`` when
        the value is unparseable or naive.
    """
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed
