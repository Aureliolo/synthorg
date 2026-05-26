"""Analytics + Reports domain MCP handlers.

8 tools backing the operator dashboards:

* ``synthorg_analytics_get_overview`` -- headline numbers for ``[since, until)``
* ``synthorg_analytics_get_trends`` -- per-metric trend directions
* ``synthorg_analytics_get_forecast`` -- budget/runway projection
* ``synthorg_metrics_get_current`` -- flat current-value map
* ``synthorg_metrics_get_history`` -- evenly-spaced sampled points
* ``synthorg_reports_list`` / ``_get`` / ``_generate`` -- report lifecycle

All analytics handlers shim through ``app_state.analytics_service``
(read-only view over :class:`SignalsService`); report handlers shim
through ``app_state.reports_service``.  Both services are wired once
at app startup.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    ok,
)
from synthorg.meta.mcp.handlers.common_args import (
    coerce_pagination,
    parse_str_sequence,
    parse_time_window,
    require_actor_id,
    require_arg,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)

_ARG_SINCE = "since"
_ARG_UNTIL = "until"
_ARG_METRIC_NAMES = "metric_names"
_ARG_HORIZON_DAYS = "horizon_days"
_ARG_SAMPLE_COUNT = "sample_count"
_MAX_SAMPLE_COUNT: Final[int] = 100
_TY_POSITIVE_INT_CAP = f"positive int <= {_MAX_SAMPLE_COUNT}"


def _reject_oversized_sample_count(value: int) -> None:
    """Raise ``ArgumentValidationError`` when ``value`` exceeds the cap.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    if value > _MAX_SAMPLE_COUNT:
        raise ArgumentValidationError(_ARG_SAMPLE_COUNT, _TY_POSITIVE_INT_CAP)


_ARG_TEMPLATE = "template"
_ARG_OPTIONS = "options"
_ARG_REPORT_ID = "report_id"

_TY_POS_INT = "positive int"
_TY_STR_SEQ = "sequence of strings"
_TY_REPORT_ID = "UUID string"
_TY_NON_BLANK = "non-blank string"

_NOT_BLANK_STR_ADAPTER = TypeAdapter(NotBlankStr)


def _parse_required_str_sequence(
    arguments: dict[str, Any],
    key: str,
) -> tuple[str, ...]:
    """Parse a required ``Sequence[str]`` argument.

    Returns:
        Tuple of the declared element types.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    result = parse_str_sequence(arguments, key)
    if result is None or len(result) == 0:
        raise ArgumentValidationError(key, _TY_STR_SEQ)
    return result


def _parse_positive_int(
    arguments: dict[str, Any],
    key: str,
    *,
    default: int,
) -> int:
    """Return parse positive int.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(key)
    if raw in (None, ""):
        return default
    if isinstance(raw, bool):
        raise ArgumentValidationError(key, _TY_POS_INT)
    if not isinstance(raw, (int, str)):
        raise ArgumentValidationError(key, _TY_POS_INT)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ArgumentValidationError(key, _TY_POS_INT) from exc
    if value < 1:
        raise ArgumentValidationError(key, _TY_POS_INT)
    return value


def _parse_str_dict(
    arguments: dict[str, Any],
    key: str,
) -> dict[str, str] | None:
    """Return parse str dict.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(key)
    if raw in (None, ""):
        return None
    if not isinstance(raw, dict):
        raise ArgumentValidationError(key, "mapping of str -> str")
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ArgumentValidationError(key, "mapping of str -> str")
        out[k] = v
    return out


def _parse_report_id(arguments: dict[str, Any]) -> UUID:
    """Return parse report id.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = require_arg(arguments, _ARG_REPORT_ID, str)
    try:
        return UUID(raw)
    except ValueError as exc:
        raise ArgumentValidationError(_ARG_REPORT_ID, _TY_REPORT_ID) from exc


# ── Analytics handlers ────────────────────────────────────────────────


async def _analytics_overview(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return analytics overview."""
    try:
        since, until = parse_time_window(arguments, until_required=False)
        result = await app_state.analytics_service.get_overview(
            since=since,
            until=until,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_analytics_get_overview",
        )
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_analytics_get_overview", exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_analytics_get_overview", exc)
        return err(exc)


async def _analytics_trends(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return analytics trends."""
    try:
        since, until = parse_time_window(arguments)
        metric_names = parse_str_sequence(arguments, _ARG_METRIC_NAMES)
        result = await app_state.analytics_service.get_trends(
            since=since,
            until=until,
            metric_names=metric_names,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_analytics_get_trends",
        )
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_analytics_get_trends", exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_analytics_get_trends", exc)
        return err(exc)


async def _analytics_forecast(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return analytics forecast."""
    try:
        since, until = parse_time_window(arguments)
        horizon_days = _parse_positive_int(
            arguments,
            _ARG_HORIZON_DAYS,
            default=30,
        )
        result = await app_state.analytics_service.get_forecast(
            since=since,
            until=until,
            horizon_days=horizon_days,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_analytics_get_forecast",
        )
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_analytics_get_forecast", exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_analytics_get_forecast", exc)
        return err(exc)


async def _metrics_current(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return metrics current."""
    try:
        since, until = parse_time_window(arguments, until_required=False)
        metric_names = parse_str_sequence(arguments, _ARG_METRIC_NAMES)
        result = await app_state.analytics_service.get_current_metrics(
            since=since,
            until=until,
            metric_names=metric_names,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_metrics_get_current",
        )
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_metrics_get_current", exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_metrics_get_current", exc)
        return err(exc)


async def _metrics_history(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return metrics history."""
    try:
        since, until = parse_time_window(arguments)
        metric_names = _parse_required_str_sequence(arguments, _ARG_METRIC_NAMES)
        sample_count = _parse_positive_int(
            arguments,
            _ARG_SAMPLE_COUNT,
            default=8,
        )
        # Cap sample_count so a single MCP call cannot fan out an
        # arbitrary number of concurrent sub-window queries against the
        # analytics service and its underlying aggregators.
        _reject_oversized_sample_count(sample_count)
        result = await app_state.analytics_service.get_metric_history(
            since=since,
            until=until,
            metric_names=metric_names,
            sample_count=sample_count,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_metrics_get_history",
        )
        return ok(result.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_metrics_get_history", exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_metrics_get_history", exc)
        return err(exc)


# ── Reports handlers ────────────────────────────────────────────────


async def _reports_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return reports list."""
    try:
        offset, limit = coerce_pagination(arguments)
        reports, total = await app_state.reports_service.list_reports(
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
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_reports_list",
        )
        return ok(dump_many(reports), pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_reports_list", exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_reports_list", exc)
        return err(exc)


async def _reports_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return reports get."""
    try:
        report_id = _parse_report_id(arguments)
        report = await app_state.reports_service.get_report(report_id)
        if report is None:
            missing = LookupError(f"Report {report_id} not found")
            # Missing-record paths are an observable error path: log
            # via the centralized helper with the requested id as
            # correlation context so operators investigating a 404 can
            # tie it to the originating request without scraping client
            # logs. Routes through safe_error_description for the
            # error message.
            log_handler_invoke_failed(
                "synthorg_reports_get",
                missing,
                report_id=str(report_id),
            )
            return err(missing, domain_code="not_found")
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_reports_get",
        )
        return ok(report.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_reports_get", exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_reports_get", exc)
        return err(exc)


async def _reports_generate(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Return reports generate.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    try:
        template_raw = require_arg(arguments, _ARG_TEMPLATE, str)
        try:
            template = _NOT_BLANK_STR_ADAPTER.validate_python(template_raw)
        except ValidationError as exc:
            raise ArgumentValidationError(_ARG_TEMPLATE, _TY_NON_BLANK) from exc
        options = _parse_str_dict(arguments, _ARG_OPTIONS)
        report = await app_state.reports_service.generate_report(
            template=template,
            author_id=require_actor_id(actor),
            options=options,
        )
        logger.info(
            MCP_HANDLER_INVOKE_SUCCESS,
            tool_name="synthorg_reports_generate",
        )
        return ok(report.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_reports_generate", exc)
        return err(exc)
    except ValueError as exc:
        log_handler_invoke_failed("synthorg_reports_generate", exc)
        return err(exc, domain_code="invalid_argument")
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed("synthorg_reports_generate", exc)
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
