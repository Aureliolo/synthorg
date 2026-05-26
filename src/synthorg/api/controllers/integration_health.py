"""Integration health API controller.

Aggregate and per-connection health endpoints for the
external service connection catalog.
"""

import asyncio
from datetime import UTC, datetime
from typing import Final

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.path_params import PathName  # noqa: TC001
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections.catalog import ConnectionCatalog  # noqa: TC001
from synthorg.integrations.connections.models import ConnectionStatus
from synthorg.integrations.health.models import HealthReport
from synthorg.integrations.health.service import check_connection_health
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import HEALTH_CHECK_FAILED

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


async def _safe_check(
    catalog: ConnectionCatalog,
    name: str,
) -> HealthReport:
    """Run a single health check with exception isolation.

    Unhandled errors inside a ``TaskGroup`` cancel the whole group;
    wrap each check so one bad connection does not fail the entire
    aggregate endpoint. MemoryError and RecursionError propagate.

    Returns:
        ``HealthReport`` instance.
    """
    try:
        return await check_connection_health(catalog, name)
    except Exception as exc:
        reraise_critical(exc)
        # Connection health checks can surface exceptions whose
        # str() embeds response bodies (including auth headers or
        # OAuth refresh tokens from the connection catalog). Log via
        # safe_error_description + error_type; never attach exc_info
        # (frame-locals can carry credential material).
        logger.warning(
            HEALTH_CHECK_FAILED,
            connection_name=name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return HealthReport(
            connection_name=name,
            status=ConnectionStatus.UNKNOWN,
            error_detail=(f"Health check raised unexpectedly: {type(exc).__name__}"),
            checked_at=datetime.now(UTC),
        )


class IntegrationHealthController(Controller):
    """Aggregate and per-connection health checks."""

    path = "/integrations/health"
    tags = ["Integrations"]  # noqa: RUF012

    @get(
        "/",
        guards=[require_read_access],
        summary="Aggregate health report across all connections",
    )
    async def aggregate_health(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[HealthReport]:
        """Return paginated health reports for connections.

        Connections are sorted by name for deterministic cursor pages,
        then probed concurrently for the requested page only -- a 100-
        connection catalog does not pay 100 upstream probes per
        request.

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from a previous page.
            limit: Page size (default 50, max defined by ``MAX_LIMIT``).

        Returns:
            Paginated response of health reports for the page's
            connections.
        """
        app_state: AppState = state.app_state
        catalog: ConnectionCatalog = app_state.connection_catalog
        connections = await catalog.list_all()
        sorted_conns = tuple(sorted(connections, key=lambda c: c.name))
        page_conns, meta = paginate_cursor(
            sorted_conns,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        if not page_conns:
            return PaginatedResponse(data=(), pagination=meta)

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(_safe_check(catalog, conn.name)) for conn in page_conns
            ]
        reports = tuple(task.result() for task in tasks)
        return PaginatedResponse(data=reports, pagination=meta)

    @get(
        "/{connection_name:str}",
        guards=[require_read_access],
        summary="Health report for a single connection",
    )
    async def single_health(
        self,
        state: State,
        connection_name: PathName,
    ) -> ApiResponse[HealthReport]:
        """Return the health report for one connection.

        ``ConnectionNotFoundError`` propagates unchanged and is
        translated by centralized exception handlers into the
        class-defined 404 + ``CONNECTION_NOT_FOUND`` envelope.

        Returns:
            ``ApiResponse[HealthReport]`` instance.
        """
        catalog = state["app_state"].connection_catalog
        report = await check_connection_health(
            catalog,
            connection_name,
        )
        return ApiResponse(data=report)
