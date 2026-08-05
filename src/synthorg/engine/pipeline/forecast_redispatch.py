# module-kind: service
"""Run the work an approved cost forecast was holding.

The forecast gate stores the work item it refused. Approving the forecast
is a decision about that specific work, so this adapter feeds it back
through the gate, which now reads the row as APPROVED, stamps the
operator's ceiling on the item, and dispatches it. Going straight to the
wrapped pipeline instead would duplicate the release rules and skip the
brief-drift check the gate performs.

The run is spawned rather than awaited: a work pipeline outlives the HTTP
request that approved its budget, exactly as ``POST /objectives`` does.
"""

import asyncio
from collections.abc import Mapping

from pydantic import JsonValue, ValidationError

from synthorg.budget.forecast_models import Forecast
from synthorg.core.boundary import parse_typed
from synthorg.engine.pipeline.models import WorkItem
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.observability import get_logger
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.budget import BUDGET_FORECAST_REDISPATCH_FAILED

logger = get_logger(__name__)


class ForecastGateRedispatcher:
    """Feeds an approved forecast's held work item back through the gate.

    Args:
        gate: The forecast gate to dispatch through. It re-reads the now
            APPROVED row, so the operator's ceiling reaches the task.
        background_tasks: Set the spawned run is tracked in, so shutdown
            can drain it and the task is not garbage-collected mid-run.
    """

    __slots__ = ("_background_tasks", "_gate")

    def __init__(
        self,
        *,
        gate: WorkPipeline,
        background_tasks: set[asyncio.Task[None]],
    ) -> None:
        self._gate = gate
        self._background_tasks = background_tasks

    async def dispatch(self, forecast: Forecast) -> None:
        """Spawn the run for the work *forecast* gated.

        Raises:
            ValidationError: When the stored work item no longer parses,
                which means the approval cannot be honoured and the
                operator must be told rather than shown a success.
        """
        stored = forecast.gated_work_item
        if stored is None:
            return
        work_item = self._rebuild(forecast, stored)
        task = asyncio.create_task(self._run(work_item))
        task.add_done_callback(
            log_task_exceptions(
                logger,
                BUDGET_FORECAST_REDISPATCH_FAILED,
                forecast_id=str(forecast.forecast_id),
            ),
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _rebuild(self, forecast: Forecast, stored: Mapping[str, JsonValue]) -> WorkItem:
        """Rebuild the held work item, bound to the forecast that released it.

        Returns:
            The parsed :class:`WorkItem` carrying ``forecast_id``, so the
            gate resolves the approved row rather than minting a new one.

        Raises:
            ValidationError: When the stored payload no longer matches the
                current :class:`WorkItem` shape.
        """
        try:
            work_item = parse_typed("budget.forecast_redispatch", stored, WorkItem)
        except ValidationError:
            logger.warning(
                BUDGET_FORECAST_REDISPATCH_FAILED,
                forecast_id=str(forecast.forecast_id),
                reason="stored_work_item_unparseable",
            )
            raise
        return work_item.model_copy(update={"forecast_id": forecast.forecast_id})

    async def _run(self, work_item: WorkItem) -> None:
        """Drive the gate and discard the terminal result.

        The caller correlates to the spawned root task through the work
        item's ``correlation_id``, which survived in the stored payload,
        so the pipeline result carries nothing the operator does not have.
        """
        await self._gate.run(work_item)


__all__ = ["ForecastGateRedispatcher"]
