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
