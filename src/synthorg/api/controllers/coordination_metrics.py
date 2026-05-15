"""Coordination metrics query controller.

Exposes ``GET /coordination/metrics`` for querying stored
coordination metrics from completed multi-agent runs.
"""

import asyncio
from datetime import datetime  # noqa: TC003
from typing import Annotated, Final

from litestar import Controller, get
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter

from synthorg.api.dto import PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    paginate_cursor,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH
from synthorg.budget.coordination_store import CoordinationMetricsRecord
from synthorg.core.domain_errors import ValidationError
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_COORDINATION_METRICS_QUERIED,
    API_VALIDATION_FAILED,
)
from synthorg.settings.enums import SettingNamespace

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50

_MAX_METRICS_QUERY: Final[int] = 10_000
"""Fallback cap applied when no settings resolver is wired in."""

# Module-level log-once guard for the settings-resolution fallback:
# during a prolonged settings outage this endpoint is queried once per
# request and would otherwise flood the logs with identical warnings.
# The flag is set on first failure and cleared on the next successful
# resolution so a later outage is visible again.
_metrics_cap_fallback_logged: bool = False


async def _resolve_metrics_cap(state: State) -> int:
    """Resolve the active metrics-query cap, falling back to the constant.

    A settings outage or malformed value must not fail the endpoint;
    the fallback constant keeps the DB-side ``LIMIT`` bounded. Warnings
    are log-once per run of failures (cleared on recovery).
    """
    global _metrics_cap_fallback_logged  # noqa: PLW0603
    app_state = state.app_state
    if not app_state.has_config_resolver:
        return _MAX_METRICS_QUERY
    try:
        result: int = await app_state.config_resolver.get_int(
            SettingNamespace.API.value, "max_metrics_per_query"
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        if not _metrics_cap_fallback_logged:
            logger.warning(
                API_VALIDATION_FAILED,
                error=(
                    "failed to resolve max_metrics_per_query;"
                    f" using fallback ({type(exc).__name__})"
                ),
                cap=_MAX_METRICS_QUERY,
            )
            _metrics_cap_fallback_logged = True
        return _MAX_METRICS_QUERY
    _metrics_cap_fallback_logged = False
    return result


class CoordinationMetricsController(Controller):
    """Query coordination metrics from completed runs."""

    path = "/coordination/metrics"
    tags = ("coordination",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_coordination_metrics(  # noqa: PLR0913
        self,
        state: State,
        task_id: Annotated[
            str | None,
            Parameter(
                max_length=QUERY_MAX_LENGTH,
                description="Filter to coordination metrics emitted under this task.",
            ),
        ] = None,
        agent_id: Annotated[
            str | None,
            Parameter(
                max_length=QUERY_MAX_LENGTH,
                description="Filter to coordination metrics emitted by this agent.",
            ),
        ] = None,
        since: Annotated[
            datetime | None,
            Parameter(
                description="Filter to metrics emitted at or after this ISO timestamp."
            ),
        ] = None,
        until: Annotated[
            datetime | None,
            Parameter(
                description="Filter to metrics emitted at or before this ISO timestamp."
            ),
        ] = None,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[CoordinationMetricsRecord]:
        """Query coordination metrics with optional filters.

        All filters are AND-combined.  Results are newest-first.
        Up to :data:`_MAX_METRICS_QUERY` records are fetched from
        the store; pagination is applied afterwards.

        Args:
            state: Application state with coordination_metrics_store.
            task_id: Filter by task identifier.
            agent_id: Filter by lead agent identifier.
            since: Exclude records before this datetime (timezone-aware).
            until: Exclude records after this datetime (timezone-aware).
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated coordination metrics.

        Raises:
            ValidationError: If *since* > *until* (HTTP 422).
        """
        if (since is not None and since.tzinfo is None) or (
            until is not None and until.tzinfo is None
        ):
            logger.warning(
                API_VALIDATION_FAILED,
                reason="naive datetime",
                since=str(since),
                until=str(until),
            )
            msg = "'since' and 'until' must be timezone-aware"
            raise ValidationError(msg)
        if since is not None and until is not None and since > until:
            logger.warning(
                API_VALIDATION_FAILED,
                reason="inverted time window",
                since=str(since),
                until=str(until),
            )
            msg = "'since' must not be after 'until'"
            raise ValidationError(msg)
        app_state = state.app_state
        metrics_cap = await _resolve_metrics_cap(state)
        entries, total_matches = app_state.coordination_metrics_store.query(
            task_id=task_id,
            agent_id=agent_id,
            since=since,
            until=until,
            limit=metrics_cap,
        )
        effective_total = min(total_matches, metrics_cap)
        page, meta = paginate_cursor(
            entries,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        logger.info(
            API_COORDINATION_METRICS_QUERIED,
            effective_total=effective_total,
            returned=len(page),
            limit=meta.limit,
            has_more=meta.has_more,
        )
        return PaginatedResponse[CoordinationMetricsRecord](
            data=page,
            pagination=meta,
        )
