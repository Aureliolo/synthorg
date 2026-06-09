"""MCP-facing coordination service.

The full multi-agent coordination pipeline runs inside the engine
loop (:class:`MultiAgentCoordinator` + :class:`CoordinationContext`).
MCP callers cannot realistically build a full context from a tool
call, so this facade exposes the read-side of coordination -- the
metrics stored by the engine after each coordination run:

- :meth:`get_task_metrics` returns the most recent metrics for
  ``task_id`` (or ``None`` if no run has been recorded); handlers map
  ``None`` onto a ``not_found`` envelope.
- :meth:`list_metrics` returns a newest-first page of the global
  metrics store alongside the total count.

Triggering coordination from MCP is out of scope -- the engine loop
owns that entry point, exposed over REST via
``POST /tasks/{task_id}/coordinate``.
"""

from synthorg.budget.coordination_store import (
    CoordinationMetricsRecord,
    CoordinationMetricsStore,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.coordination_metrics import (
    COORD_METRICS_INVALID_REQUEST,
    COORD_METRICS_RECORD_FETCHED,
)

logger = get_logger(__name__)


class CoordinationService:
    """Read-side facade over :class:`CoordinationMetricsStore`.

    Constructor:
        metrics_store: The shared in-memory metrics store populated by
            :class:`MultiAgentCoordinator` after each coordination
            run.
    """

    __slots__ = ("_metrics_store",)

    def __init__(
        self,
        *,
        metrics_store: CoordinationMetricsStore,
    ) -> None:
        """Initialise with the metrics store dependency."""
        self._metrics_store = metrics_store

    async def get_task_metrics(
        self,
        task_id: NotBlankStr,
    ) -> CoordinationMetricsRecord | None:
        """Return the most recent metrics for *task_id*, or ``None``.

        Args:
            task_id: Task identifier to look up.

        Returns:
            The newest recorded metrics record for this task, or
            ``None`` if no coordination run has been recorded.
        """
        records, _ = self._metrics_store.query(
            task_id=str(task_id),
            limit=1,
        )
        if not records:
            return None
        record = records[0]
        logger.debug(
            COORD_METRICS_RECORD_FETCHED,
            task_id=record.task_id,
            agent_id=record.agent_id,
            team_size=record.team_size,
            surface="mcp.get_task_metrics",
        )
        return record

    async def list_metrics(
        self,
        *,
        offset: int,
        limit: int,
    ) -> tuple[tuple[CoordinationMetricsRecord, ...], int]:
        """Return a newest-first page of metrics + the total count.

        :meth:`CoordinationMetricsStore.query` caps its own result set
        at ``limit``, so the page is sliced before ``offset`` is
        applied. ``total`` is the store's own unfiltered match count
        and therefore always reflects every record the store has
        retained (not the page slice).

        Args:
            offset: Page offset (>= 0).
            limit: Page size (> 0).

        Returns:
            Tuple of ``(page, total)``.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` is not
                strictly positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            logger.warning(
                COORD_METRICS_INVALID_REQUEST,
                param="offset",
                value=offset,
                surface="mcp.list_metrics",
            )
            raise ValueError(msg)
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                COORD_METRICS_INVALID_REQUEST,
                param="limit",
                value=limit,
                surface="mcp.list_metrics",
            )
            raise ValueError(msg)
        # Query enough rows to cover offset + limit; the store returns
        # newest-first already.
        page_rows, total = self._metrics_store.query(limit=offset + limit)
        page = tuple(page_rows[offset : offset + limit])
        return page, total


__all__ = ["CoordinationService"]
