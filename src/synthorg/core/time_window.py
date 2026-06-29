# module-kind: code
"""Parsing for rolling-window day labels (``'7d'``, ``'30d'``, ``'90d'``).

The performance, trust, and meta-signal subsystems each re-derived a
``<N>d`` parser with subtly different strictness. These two helpers are
the single source of truth: one anchored digits-then-``d`` pattern
rejects malformed labels uniformly.
"""

import re
from typing import Final

_WINDOW_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(\d+)d$")

#: The standard rolling-window labels the performance subsystem aggregates over
#: by default. Centralised here (the single source of truth for window labels)
#: so the trust milestone gate can validate ``clean_history_days`` against the
#: same vocabulary the snapshots actually produce.
DEFAULT_WINDOW_LABELS: Final[tuple[str, ...]] = ("7d", "30d", "90d")


def parse_window_days(window_size: str) -> int | None:
    """Return the day count from a ``'<N>d'`` label.

    Args:
        window_size: Window label such as ``'7d'``.

    Returns:
        The day count, or ``None`` when the label is not ``'<N>d'``.
    """
    match = _WINDOW_PATTERN.match(window_size)
    return int(match.group(1)) if match else None


def parse_window_days_strict(window_size: str) -> int:
    """Return the day count from a ``'<N>d'`` label, rejecting bad input.

    Args:
        window_size: Window label such as ``'7d'``.

    Returns:
        The day count.

    Raises:
        ValueError: When the label is not ``'<N>d'``.
    """
    days = parse_window_days(window_size)
    if days is None:
        msg = f"Unrecognised window size format: {window_size!r}. Expected '<N>d'."
        raise ValueError(msg)
    return days
