"""ISO 8601 datetime parsers for non-persistence ingestion points.

The persistence boundary uses ``parse_iso_utc`` (rejects naive, converts
aware to UTC). Two non-persistence ingestion shapes recur often enough to
have been re-implemented inline at several call sites; both live here so
the pattern is written once:

* :func:`parse_iso_assume_utc` -- for externally-authored timestamps that
  may legitimately omit an offset (Mem0 records, OAuth provider
  metadata): a naive value is assumed UTC rather than rejected, and an
  already-aware value is preserved as-is.
* :func:`parse_git_log_timestamp` -- for ``git log`` author/commit dates,
  which always carry an explicit offset: a naive value signals a
  malformed row and is rejected (``None``), and the original offset is
  preserved.

Both keep an already-aware datetime exactly as parsed (no UTC
re-normalisation) so callers that store the value keep the source offset.

Two further one-liners centralise patterns that recurred across the
codebase:

* :func:`is_valid_iso_datetime` -- a boolean probe wrapping the
  ``datetime.fromisoformat`` try/except that several validators
  duplicated.
* :func:`now_iso_utc` -- the canonical ``datetime.now(UTC).isoformat()``
  current-timestamp string.
"""

from datetime import UTC, datetime


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


def is_valid_iso_datetime(value: str) -> bool:
    """Return whether ``value`` parses as an ISO 8601 datetime.

    Centralises the ``datetime.fromisoformat`` try/except guard that
    several validators duplicated. Offset presence is not checked; this
    is a parseability probe only.

    Args:
        value: The candidate ISO 8601 string.

    Returns:
        ``True`` when ``value`` parses, ``False`` otherwise.
    """
    try:
        datetime.fromisoformat(value)
    except ValueError, TypeError:
        return False
    return True


def now_iso_utc() -> str:
    """Return the current UTC instant as an ISO 8601 string.

    The canonical replacement for inline ``datetime.now(UTC).isoformat()``
    expressions.

    Returns:
        The current time formatted as an ISO 8601 string with a UTC
        offset.
    """
    return datetime.now(UTC).isoformat()
