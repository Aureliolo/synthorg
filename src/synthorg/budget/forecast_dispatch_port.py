# module-kind: code
"""Port for running the work an approved forecast was blocking.

The forecast gate refuses a brief and persists the work item alongside the
estimate. Approval is therefore a decision about work that already exists,
and the only honest completion of that decision is to run it. This port is
how the budget layer says "run this" without learning what a work pipeline
is: the implementation lives with the pipeline, above the budget layer.
"""

from typing import Protocol, runtime_checkable

from synthorg.budget.forecast_models import Forecast


@runtime_checkable
class ApprovedForecastDispatcher(Protocol):
    """Runs the work item an approved forecast gated."""

    async def dispatch(self, forecast: Forecast) -> None:
        """Re-dispatch the work *forecast* was blocking.

        Implementations spawn the run rather than awaiting it: a work
        pipeline outlives the HTTP request that approved its budget.

        Raises:
            DomainError: When the work cannot be dispatched at all, so the
                approving operator learns the work did not start instead
                of reading a success that dropped it.
        """
        ...


__all__ = ["ApprovedForecastDispatcher"]
