"""Shared ``Retry-After`` delta validation.

Several layers parse a ``Retry-After`` hint from a different source (an
exception attribute, an ``httpx.Headers`` mapping, a provider exception's
header bag) but all need the same final check: the candidate must be a
finite, non-negative number of seconds. ``inf`` / ``nan`` would crash
header serialisation or produce a non-terminating backoff, and a negative
delay (e.g. a past HTTP-date) is a benign "retry now" with no real hint.

This validator centralises that one rule so every caller agrees on what a
valid delta is. Each caller keeps its own source-specific extraction and
return-type shaping (an ``int`` ceil at the HTTP surface, a ``float``
elsewhere).
"""

import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def parse_retry_after_seconds(
    raw: object,
    now: datetime | None = None,
) -> float | None:
    """Parse a ``Retry-After`` value into a delay in seconds.

    Accepts both RFC 9110 10.2.3 forms: a delta-seconds number
    (``"120"``) and an HTTP-date (``"Wed, 21 Oct 2026 07:28:00 GMT"``).
    An HTTP-date is converted to the delay from ``now``; a past date
    yields a negative delay, which a caller's finite/non-negative guard
    (:func:`coerce_finite_nonneg_seconds`) rejects. Returns ``None`` when
    the value matches neither form.

    Args:
        raw: The raw header value (delta-seconds string or HTTP-date).
        now: Reference instant for the HTTP-date delta; defaults to the
            current UTC time. Injectable so tests are deterministic
            without depending on wall-clock timing.

    Returns:
        The parsed seconds (possibly negative for a past date), or
        ``None`` when unparseable.
    """
    try:
        return float(raw)  # type: ignore[arg-type]
    except ValueError, TypeError, OverflowError:
        pass
    if not isinstance(raw, str):
        return None
    try:
        retry_dt = parsedate_to_datetime(raw)
    except ValueError, TypeError:
        return None
    if retry_dt.tzinfo is None:
        retry_dt = retry_dt.replace(tzinfo=UTC)
    current = now if now is not None else datetime.now(UTC)
    return (retry_dt - current).total_seconds()


def coerce_finite_nonneg_seconds(raw: object) -> float | None:
    """Coerce ``raw`` to a finite, non-negative seconds float.

    Args:
        raw: A candidate delay. Booleans and non-numeric values are
            rejected; ``inf`` / ``nan`` and negatives are rejected.

    Returns:
        The value as a ``float`` when it is a finite ``>= 0`` number,
        otherwise ``None``.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        return None
    return value
