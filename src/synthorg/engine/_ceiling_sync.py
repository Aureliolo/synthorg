"""Resolve the hard ceiling a run is actually enforced against.

``Task.hard_ceiling`` is the value captured at intake. The linked
forecast row is what an operator raises to release a run parked on
spend, and nothing rewrites the task, so enforcement that reads only the
task would re-park a resumed run on the ceiling that stopped it.
"""

# module-kind: code

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.budget import BUDGET_HARD_CEILING_RAISED
from synthorg.observability.events.execution import EXECUTION_ENGINE_BUDGET_STOPPED
from synthorg.persistence.cost_forecast_protocol import CostForecastRepository

logger = get_logger(__name__)

_CEILING_TOLERANCE: Final[float] = 1e-9
"""Below this, two stored amounts are the same ceiling.

Both values are floats round-tripped through persistence, so an exact
``==`` would treat representation noise as an operator decision.
"""


async def ceiling_synced_task(
    task: Task,
    repo: CostForecastRepository | None,
) -> Task:
    """Return *task* carrying the operator's current hard ceiling.

    Only a strictly higher forecast ceiling is adopted. A forecast that
    cannot be read, or that sits at or below the snapshot, leaves the
    task alone: enforcement then keeps the stricter of the two, so the
    run parks again rather than spending past a limit nobody raised.

    Args:
        task: The task about to be enforced against.
        repo: Forecast persistence, or ``None`` when unwired.

    Returns:
        The task, with ``hard_ceiling`` refreshed when the linked
        forecast carries a higher one.
    """
    if repo is None or task.forecast_id is None:
        return task
    try:
        forecast = await repo.get(task.forecast_id)
    except Exception as read_exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- degrades to the stricter snapshot ceiling
        reraise_critical(read_exc)
        logger.warning(
            EXECUTION_ENGINE_BUDGET_STOPPED,
            task_id=str(task.id),
            note="forecast ceiling read failed; enforcing the task snapshot",
            error_type=type(read_exc).__name__,
            error=safe_error_description(read_exc),
        )
        return task
    if forecast is None or forecast.ceiling_amount is None:
        return task
    snapshot = task.hard_ceiling
    if (
        snapshot is not None
        and forecast.ceiling_amount - snapshot <= _CEILING_TOLERANCE
    ):
        return task
    logger.debug(
        BUDGET_HARD_CEILING_RAISED,
        task_id=str(task.id),
        forecast_id=str(task.forecast_id),
        new_ceiling=forecast.ceiling_amount,
        note="raised ceiling applied to the run",
    )
    return task.model_copy(update={"hard_ceiling": forecast.ceiling_amount})


__all__ = ["ceiling_synced_task"]
