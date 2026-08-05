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
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.models import WorkItem
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.notifications.protocol import NotificationDispatcherProtocol
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.budget import BUDGET_FORECAST_REDISPATCH_FAILED

logger = get_logger(__name__)

#: Source label on the operator notification, so a sink can route it.
_SOURCE: NotBlankStr = NotBlankStr("budget.forecast_redispatch")


class ForecastGateRedispatcher:
    """Feeds an approved forecast's held work item back through the gate.

    Args:
        gate: The forecast gate to dispatch through. It re-reads the now
            APPROVED row, so the operator's ceiling reaches the task.
        background_tasks: Set the spawned run is tracked in, so shutdown
            can drain it and the task is not garbage-collected mid-run.
        notifications: Operator alert sink for a run that failed after the
            approval already returned 200. ``None`` leaves the ERROR log as
            the only record.
    """

    __slots__ = ("_background_tasks", "_gate", "_notifications")

    def __init__(
        self,
        *,
        gate: WorkPipeline,
        background_tasks: set[asyncio.Task[None]],
        notifications: NotificationDispatcherProtocol | None = None,
    ) -> None:
        self._gate = gate
        self._background_tasks = background_tasks
        self._notifications = notifications

    async def dispatch(self, forecast: Forecast) -> None:
        """Spawn the run for the work *forecast* gated.

        Raises:
            ServiceUnavailableError: When the stored work item no longer
                parses, which means the approval cannot be honoured and the
                operator must be told rather than shown a success.
        """
        stored = forecast.gated_work_item
        if stored is None:
            logger.warning(
                BUDGET_FORECAST_REDISPATCH_FAILED,
                forecast_id=str(forecast.forecast_id),
                reason="no_gated_work_item",
            )
            return
        work_item = self._rebuild(forecast, stored)
        task = asyncio.create_task(self._run(forecast, work_item))
        # Backstop only: _run reports its own failure with the work item's
        # identifiers, so anything reaching here escaped that path.
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
            ServiceUnavailableError: When the stored payload no longer
                matches the current :class:`WorkItem` shape. Translated from
                the validation failure so the approval path raises a domain
                error rather than leaking a third-party exception through a
                port that promises one.
        """
        try:
            work_item = parse_typed("budget.forecast_redispatch", stored, WorkItem)
        except ValidationError as exc:
            msg = (
                f"Cost forecast {forecast.forecast_id} holds work that no longer"
                f" matches the current shape; the approved work cannot be run"
            )
            logger.error(
                BUDGET_FORECAST_REDISPATCH_FAILED,
                forecast_id=str(forecast.forecast_id),
                reason="stored_work_item_unparseable",
                error_type=type(exc).__name__,
            )
            raise ServiceUnavailableError(msg) from exc
        return work_item.model_copy(update={"forecast_id": forecast.forecast_id})

    async def _run(self, forecast: Forecast, work_item: WorkItem) -> None:
        """Drive the gate and discard the terminal result.

        The caller correlates to the spawned root task through the work
        item's ``correlation_id``, which survived in the stored payload,
        so the pipeline result carries nothing the operator does not have.

        A failure here happens after the approval already returned 200, so
        nothing is left to raise into: the operator is holding an approved
        forecast whose work never ran. It is reported with the work item's
        own identifiers (a ``forecast_id`` alone points at no brief, project
        or trace) and raised as an operator notification.
        """
        try:
            await self._gate.run(work_item)
        except Exception as exc:  # noqa: BLE001 -- reported to the operator
            # lint-allow: swallow-ok -- the approval already returned 200, so
            # there is no caller left to raise into; _report_failed_run is the
            # surfacing path (ERROR log naming the work item, plus an operator
            # notification), and re-raising here would only duplicate it
            # through the task's done-callback backstop.
            reraise_critical(exc)
            await self._report_failed_run(forecast, work_item, exc)

    async def _report_failed_run(
        self,
        forecast: Forecast,
        work_item: WorkItem,
        exc: Exception,
    ) -> None:
        """Log the failed run and alert the operator holding the approval."""
        detail = safe_error_description(exc)
        logger.error(
            BUDGET_FORECAST_REDISPATCH_FAILED,
            forecast_id=str(forecast.forecast_id),
            reason="approved_work_run_failed",
            correlation_id=str(work_item.correlation_id),
            project=str(work_item.project),
            requested_by=str(work_item.requested_by),
            title=str(work_item.title),
            error_type=type(exc).__name__,
            error=detail,
        )
        if self._notifications is None:
            return
        await self._notifications.dispatch(
            Notification(
                category=NotificationCategory.BUDGET,
                severity=NotificationSeverity.ERROR,
                title=NotBlankStr(f"Approved forecast did not run: {work_item.title}"),
                body=(
                    f"The work approved on cost forecast {forecast.forecast_id}"
                    f" failed to run and has not been retried.\n"
                    f"Project: {work_item.project}\n"
                    f"Requested by: {work_item.requested_by}\n"
                    f"Correlation id: {work_item.correlation_id}\n"
                    f"Failure: {detail}"
                ),
                source=_SOURCE,
                metadata={
                    "forecast_id": str(forecast.forecast_id),
                    "correlation_id": str(work_item.correlation_id),
                    "project": str(work_item.project),
                },
            )
        )


__all__ = ["ForecastGateRedispatcher"]
