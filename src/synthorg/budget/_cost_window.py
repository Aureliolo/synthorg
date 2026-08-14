# module-kind: code
"""Shared cost-window leaf helpers for the forecaster and its lookups.

The :class:`~synthorg.budget.forecaster.CostForecaster`, its
``CostTrackerHistoryLookup``, and the ``AgentRegistryAssignmentLookup``
all reason over the same rolling cost-observation window, the same clock
seam, and the same canonical model-id capability extraction. Centralising
those leaves here keeps the three modules from drifting (a change to the
window width or the archetype rules now lands in one place).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Literal

from synthorg.budget.model_capability import heuristic_capability, heuristic_is_local

#: Rolling window (days) of cost observations the forecaster and its
#: lookups reason over.
COST_WINDOW_DAYS: Final[int] = 30

#: Clock seam returning a UTC datetime.
ClockFn = Callable[[], datetime]

#: What per-turn cost is bucketed by. The three capability rungs, plus
#: ``local``: a model an operator hosts is billed by nobody whatever it can
#: do, so its observations would drag any rung's average toward zero if they
#: shared a bucket. Locality therefore takes precedence here, and only here,
#: because this is a claim about price rather than about capability.
CostBucket = Literal["basic", "capable", "expert", "local"]

#: Bucket for a model the archetype heuristic does not recognise. The middle
#: rung rather than the cheapest: an unknown model that turns out expensive
#: should surprise an operator by less than one assumed free.
DEFAULT_COST_BUCKET: Final[CostBucket] = "capable"


def utc_now() -> datetime:
    """Return the current UTC timestamp (default :data:`ClockFn`).

    Returns:
        The current time in UTC.
    """
    return datetime.now(UTC)


def cost_bucket_for_model_id(model_id: str) -> CostBucket | None:
    """Best-effort cost bucket for a canonical archetype model id.

    Returns:
        ``local`` for a locally-hosted archetype, else its rung, or
        ``None`` when the id matches no archetype at all.
    """
    if heuristic_is_local(model_id):
        return "local"
    return heuristic_capability(model_id)


__all__ = [
    "COST_WINDOW_DAYS",
    "DEFAULT_COST_BUCKET",
    "ClockFn",
    "CostBucket",
    "cost_bucket_for_model_id",
    "utc_now",
]
