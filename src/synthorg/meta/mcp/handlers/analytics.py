"""Analytics + Reports domain MCP handlers.

8 tools backing the operator dashboards:

* ``synthorg_analytics_get_overview`` -- headline numbers for ``[since, until)``
* ``synthorg_analytics_get_trends`` -- per-metric trend directions
* ``synthorg_analytics_get_forecast`` -- budget/runway projection
* ``synthorg_metrics_get_current`` -- flat current-value map
* ``synthorg_metrics_get_history`` -- evenly-spaced sampled points
* ``synthorg_reports_list`` / ``_get`` / ``_generate`` -- report lifecycle

All analytics handlers shim through ``analytics_service_of(app_state)``
(read-only view over :class:`SignalsService`); report handlers shim
through ``reports_service_of(app_state)``.  Both services are wired once
at app startup.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.mcp.domains._simple_args import (
    AnalyticsGetForecastArgs,
    AnalyticsGetOverviewArgs,
    AnalyticsGetTrendsArgs,
    MetricsGetCurrentArgs,
    MetricsGetHistoryArgs,
    ReportsGenerateArgs,
    ReportsGetArgs,
    ReportsListArgs,
)
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    ok,
)
from synthorg.meta.mcp.handlers.common_args import (
    require_actor_id,
    resolve_time_window,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.meta.state import analytics_service_of, reports_service_of
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_REPORT_ID = "report_id"
_TY_REPORT_ID = "UUID string"


def _parse_report_id(raw: str) -> UUID:
    """Coerce a validated ``report_id`` string into a :class:`UUID`.

    Returns:
        The parsed ``UUID``.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ArgumentValidationError(_ARG_REPORT_ID, _TY_REPORT_ID) from exc


# ── Analytics handlers ────────────────────────────────────────────────


async def _analytics_overview(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return analytics overview."""
    tool = "synthorg_analytics_get_overview"
    try:
        args = typed_args(arguments, AnalyticsGetOverviewArgs)
        since, until = resolve_time_window(args.since, args.until, until_required=False)
        result = await analytics_service_of(app_state).get_overview(
            since=since,
            until=until,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _analytics_trends(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return analytics trends."""
    tool = "synthorg_analytics_get_trends"
    try:
        args = typed_args(arguments, AnalyticsGetTrendsArgs)
        since, until = resolve_time_window(args.since, args.until)
        result = await analytics_service_of(app_state).get_trends(
            since=since,
            until=until,
            metric_names=args.metric_names,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _analytics_forecast(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return analytics forecast."""
    tool = "synthorg_analytics_get_forecast"
    try:
        args = typed_args(arguments, AnalyticsGetForecastArgs)
        since, until = resolve_time_window(args.since, args.until)
        result = await analytics_service_of(app_state).get_forecast(
            since=since,
            until=until,
            horizon_days=args.horizon_days,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _metrics_current(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return metrics current."""
    tool = "synthorg_metrics_get_current"
    try:
        args = typed_args(arguments, MetricsGetCurrentArgs)
        since, until = resolve_time_window(args.since, args.until, until_required=False)
        result = await analytics_service_of(app_state).get_current_metrics(
            since=since,
            until=until,
            metric_names=args.metric_names,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _metrics_history(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return metrics history."""
    tool = "synthorg_metrics_get_history"
    try:
        args = typed_args(arguments, MetricsGetHistoryArgs)
        since, until = resolve_time_window(args.since, args.until)
        result = await analytics_service_of(app_state).get_metric_history(
            since=since,
            until=until,
            metric_names=args.metric_names,
            sample_count=args.sample_count,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


# ── Reports handlers ────────────────────────────────────────────────


async def _reports_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return reports list."""
    tool = "synthorg_reports_list"
    try:
        page_args = typed_args(arguments, ReportsListArgs)
        offset, limit = page_args.offset, page_args.limit
        reports, total = await reports_service_of(app_state).list_reports(
            offset=offset,
            limit=limit,
        )
        # ``reports_service.list_reports`` already returns the requested
        # page (offset/limit applied service-side) plus the unfiltered
        # ``total`` count.  Build the pagination envelope directly from
        # that slice -- do NOT re-slice with ``paginate_sequence`` or
        # page 2+ requests will apply the offset a second time and come
        # back empty.
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
        return ok(dump_many(reports), pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _reports_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return reports get."""
    tool = "synthorg_reports_get"
    try:
        report_id = _parse_report_id(typed_args(arguments, ReportsGetArgs).report_id)
        report = await reports_service_of(app_state).get_report(report_id)
        if report is None:
            missing = LookupError(f"Report {report_id} not found")
            # Missing-record paths are an observable error path: log
            # via the centralized helper with the requested id as
            # correlation context so operators investigating a 404 can
            # tie it to the originating request without scraping client
            # logs. Routes through safe_error_description for the
            # error message.
            log_handler_invoke_failed(tool, missing, report_id=str(report_id))
            return err(missing, domain_code="not_found")
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
        return ok(report.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _reports_generate(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Return reports generate.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    tool = "synthorg_reports_generate"
    try:
        args = typed_args(arguments, ReportsGenerateArgs)
        report = await reports_service_of(app_state).generate_report(
            template=args.template,
            author_id=require_actor_id(actor),
            options=args.options,
        )
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
        return ok(report.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except ValueError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="invalid_argument")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


ANALYTICS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_analytics_get_overview": _analytics_overview,
        "synthorg_analytics_get_trends": _analytics_trends,
        "synthorg_analytics_get_forecast": _analytics_forecast,
        "synthorg_metrics_get_current": _metrics_current,
        "synthorg_metrics_get_history": _metrics_history,
        "synthorg_reports_list": _reports_list,
        "synthorg_reports_get": _reports_get,
        "synthorg_reports_generate": _reports_generate,
    },
)
