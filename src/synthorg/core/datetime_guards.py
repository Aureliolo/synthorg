"""Datetime range and window validation guards.

Two ordering guards recurred inline across the budget, classification,
and persistence query paths. They differ in strictness:

* :func:`validate_datetime_range` accepts optional bounds and only
  rejects an inverted range when both are present. It does not require
  tz-awareness (callers that need it normalise upstream).
* :func:`validate_time_window` requires both bounds, rejects naive
  values, and rejects an inverted or empty window. Scan helpers use it
  to fail fast before iterating.
"""

from datetime import datetime


def validate_datetime_range(
    start: datetime | None,
    end: datetime | None,
    *,
    start_label: str = "start",
    end_label: str = "end",
) -> None:
    """Reject an inverted range when both bounds are present.

    Args:
        start: The lower bound, or ``None`` for an open start.
        end: The upper bound, or ``None`` for an open end.
        start_label: Field name for the lower bound in the error message.
        end_label: Field name for the upper bound in the error message.

    Raises:
        ValueError: When both bounds are present and ``start >= end``.
    """
    if start is not None and end is not None and start >= end:
        msg = (
            f"{start_label} ({start.isoformat()}) must be before "
            f"{end_label} ({end.isoformat()})"
        )
        raise ValueError(msg)


def validate_time_window(since: datetime, until: datetime) -> None:
    """Reject a naive, inverted, or empty time window.

    Args:
        since: The window start (must be tz-aware).
        until: The window end (must be tz-aware and after ``since``).

    Raises:
        ValueError: When either bound is naive or when
            ``since >= until``.
    """
    if since.tzinfo is None or until.tzinfo is None:
        msg = "since/until must be timezone-aware"
        raise ValueError(msg)
    if since >= until:
        msg = (
            f"since ({since.isoformat()}) must be earlier than until "
            f"({until.isoformat()})"
        )
        raise ValueError(msg)
