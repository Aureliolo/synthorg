# module-kind: code
"""Shared cost-window leaf helpers for the forecaster and its lookups.

The :class:`~synthorg.budget.forecaster.CostForecaster`, its
``CostTrackerHistoryLookup``, and the ``AgentRegistryAssignmentLookup``
all reason over the same rolling cost-observation window, the same clock
seam, and the same canonical model-id tier extraction. Centralising those
leaves here keeps the three modules from drifting (a change to the window
width or the tier rules now lands in one place).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

#: Rolling window (days) of cost observations the forecaster and its
#: lookups reason over.
COST_WINDOW_DAYS: Final[int] = 30

#: Clock seam returning a UTC datetime.
ClockFn = Callable[[], datetime]


def utc_now() -> datetime:
    """Return the current UTC timestamp (default :data:`ClockFn`).

    Returns:
        The current time in UTC.
    """
    return datetime.now(UTC)


def tier_from_model_id(model_id: str) -> str | None:
    """Best-effort tier extraction from a canonical model id.

    Canonical model ids follow ``example-<tier>-<rev>``; we read the
    tier suffix. Unknown patterns return ``None`` and the caller
    falls back to ``medium``.

    Returns:
        The resulting ``str``, or ``None`` when unavailable.
    """
    parts = model_id.split("-")
    if len(parts) < 2:  # noqa: PLR2004 -- canonical id requires at least two parts
        return None
    # Check local-small before the plain-tier set: a canonical
    # ``example-local-small-001`` id has ``parts[-2] == "small"``, so the
    # bare-tier branch would otherwise shadow the local-small case.
    if "local" in parts and "small" in parts:
        return "local-small"
    candidate = parts[-2].lower()
    if candidate in {"large", "medium", "small"}:
        return candidate
    return None


__all__ = ["COST_WINDOW_DAYS", "ClockFn", "tier_from_model_id", "utc_now"]
