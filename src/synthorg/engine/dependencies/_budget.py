# module-kind: declarative
"""What measures and bounds a run's spend."""

from dataclasses import dataclass
from typing import Final

from synthorg.budget.coordination_collector import CoordinationMetricsCollector
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.persistence.cost_forecast_protocol import CostForecastRepository

#: Refused rather than reconciled: two trackers means the pre-flight check and
#: the in-flight checker measure different totals, and the run is bounded by
#: whichever one the caller happened to hand the tighter number.
_TRACKER_MISMATCH: Final[str] = (
    "cost_tracker must be the same object as budget_enforcer.cost_tracker: "
    "an enforcer bounding one total while the engine records into another "
    "leaves the run bounded by neither"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineBudget:
    """The tracker, the enforcer and the forecast store.

    Attributes:
        cost_tracker: Where every call's ``CostRecord`` lands, or ``None``
            when this engine records no spend at all.
        budget_enforcer: Pre-flight and in-flight bounds. ``None`` leaves
            the run bounded only by the task's own ``budget_limit`` and
            ``hard_token_ceiling``.
        cost_forecast_repo: Durable forecasts, or ``None``.
        coordination_metrics_collector: The single-agent baselines the
            multi-agent metrics compare against, or ``None`` when no
            coordinator shares this engine.
    """

    cost_tracker: CostTrackerProtocol | None
    budget_enforcer: BudgetEnforcer | None
    cost_forecast_repo: CostForecastRepository | None
    coordination_metrics_collector: CoordinationMetricsCollector | None

    def __post_init__(self) -> None:
        """Refuse an enforcer and a tracker that measure different totals.

        Raises:
            ValueError: When both are supplied and they are not the same
                object.
        """
        enforcer = self.budget_enforcer
        if (
            enforcer is not None
            and self.cost_tracker is not None
            and self.cost_tracker is not enforcer.cost_tracker
        ):
            raise ValueError(_TRACKER_MISMATCH)

    @property
    def effective_tracker(self) -> CostTrackerProtocol | None:
        """The tracker a run actually records into.

        The enforcer's own tracker wins when one is wired, which
        :meth:`__post_init__` has already established is the same object
        whenever the caller named both.

        Returns:
            The enforcer's tracker when an enforcer is wired, else the
            declared tracker.
        """
        if self.budget_enforcer is not None:
            return self.budget_enforcer.cost_tracker
        return self.cost_tracker


__all__ = ["EngineBudget"]
