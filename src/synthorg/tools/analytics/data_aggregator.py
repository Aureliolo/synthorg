"""Data aggregator tool -- query and aggregate analytics data via provider.

The ``AnalyticsProvider`` protocol defines a vendor-agnostic interface
for querying analytics backends.  No concrete implementation is
shipped -- users inject a provider at construction time.
"""

import asyncio
from datetime import datetime
from typing import Any, ClassVar, Final, Protocol, runtime_checkable

from pydantic import BaseModel  # noqa: TC002 -- ClassVar type at runtime

from synthorg.core.enums import ActionType
from synthorg.observability import get_logger
from synthorg.observability.events.analytics import (
    ANALYTICS_TOOL_PROVIDER_NOT_CONFIGURED,
    ANALYTICS_TOOL_QUERY_FAILED,
    ANALYTICS_TOOL_QUERY_START,
    ANALYTICS_TOOL_QUERY_SUCCESS,
)
from synthorg.tools.analytics._args import DataAggregatorArgs
from synthorg.tools.analytics.base_analytics_tool import BaseAnalyticsTool
from synthorg.tools.analytics.config import AnalyticsToolsConfig  # noqa: TC001
from synthorg.tools.base import ToolExecutionResult

logger = get_logger(__name__)

_VALID_PERIODS: Final[frozenset[str]] = frozenset({"7d", "30d", "90d", "custom"})

_VALID_GROUP_BY: Final[frozenset[str]] = frozenset(
    {"day", "week", "month", "agent", "department"}
)


@runtime_checkable
class AnalyticsProvider(Protocol):
    """Abstracted analytics data provider protocol.

    Implementations must be async and return query results
    as a dictionary.
    """

    async def query(
        self,
        *,
        metrics: list[str],
        period: str,
        group_by: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Query analytics data.

        Args:
            metrics: Metric names to aggregate.
            period: Time period (7d, 30d, 90d, or custom).
            group_by: Optional grouping dimension.
            start_date: Start date for custom period (ISO 8601).
            end_date: End date for custom period (ISO 8601).

        Returns:
            Query results as a dictionary.
        """
        ...


_PARAMETERS_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "metrics": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Metric names to aggregate (e.g. 'total_cost', 'task_completion_rate')"
            ),
        },
        "period": {
            "type": "string",
            "enum": sorted(_VALID_PERIODS),
            "description": "Time period for aggregation",
        },
        "group_by": {
            "type": "string",
            "enum": sorted(_VALID_GROUP_BY),
            "description": "Optional grouping dimension",
        },
        "start_date": {
            "type": "string",
            "description": "Start date for custom period (ISO 8601)",
        },
        "end_date": {
            "type": "string",
            "description": "End date for custom period (ISO 8601)",
        },
    },
    "required": ["metrics", "period"],
    "additionalProperties": False,
}


class DataAggregatorTool(BaseAnalyticsTool):
    """Query and aggregate analytics data via a provider.

    Requires an ``AnalyticsProvider`` to be injected at construction
    time.  Validates metric names against the optional whitelist
    in ``AnalyticsToolsConfig``.

    Examples:
        Query metrics::

            tool = DataAggregatorTool(provider=my_provider)
            result = await tool.execute(
                arguments={
                    "metrics": ["total_cost", "task_count"],
                    "period": "7d",
                    "group_by": "day",
                }
            )
    """

    args_model: ClassVar[type[BaseModel] | None] = DataAggregatorArgs

    def __init__(
        self,
        *,
        provider: AnalyticsProvider | None = None,
        config: AnalyticsToolsConfig | None = None,
    ) -> None:
        """Initialize the data aggregator tool with the typed args schema."""
        super().__init__(
            name="data_aggregator",
            description=(
                "Query and aggregate analytics data "
                "(costs, tasks, performance metrics)."
            ),
            parameters_schema=DataAggregatorArgs.model_json_schema(),
            action_type=ActionType.CODE_READ,
            config=config,
        )
        self._provider = provider

    def _validate_query_params(
        self,
        metrics: list[str],
        period: str,
        group_by: str | None,
        start_date: str | None,
        end_date: str | None,
    ) -> ToolExecutionResult | None:
        """Validate query parameters.

        Returns a ``ToolExecutionResult`` error if validation fails,
        or ``None`` if all parameters are valid.
        """
        blocked = [m for m in metrics if not self._is_metric_allowed(m)]
        if blocked:
            logger.warning(
                ANALYTICS_TOOL_QUERY_FAILED,
                error="metrics_not_allowed",
                blocked=blocked,
            )
            return ToolExecutionResult(
                content=(
                    f"Metrics not allowed: {blocked}. "
                    f"Allowed: {sorted(self._config.allowed_metrics or set())}"
                ),
                is_error=True,
            )

        if period not in _VALID_PERIODS:
            logger.warning(
                ANALYTICS_TOOL_QUERY_FAILED,
                error="invalid_period",
                period=period,
            )
            return ToolExecutionResult(
                content=(
                    f"Invalid period: {period!r}. "
                    f"Must be one of: {sorted(_VALID_PERIODS)}"
                ),
                is_error=True,
            )

        if period == "custom" and (not start_date or not end_date):
            logger.warning(
                ANALYTICS_TOOL_QUERY_FAILED,
                error="missing_custom_dates",
            )
            return ToolExecutionResult(
                content="Custom period requires both start_date and end_date.",
                is_error=True,
            )

        for date_label, date_val in (
            ("start_date", start_date),
            ("end_date", end_date),
        ):
            if date_val is not None:
                try:
                    datetime.fromisoformat(date_val)
                except ValueError:
                    logger.warning(
                        ANALYTICS_TOOL_QUERY_FAILED,
                        error="invalid_date",
                        field=date_label,
                        value=date_val,
                    )
                    return ToolExecutionResult(
                        content=(
                            f"Invalid {date_label}: {date_val!r}. "
                            f"Must be ISO 8601 format."
                        ),
                        is_error=True,
                    )

        if group_by is not None and group_by not in _VALID_GROUP_BY:
            logger.warning(
                ANALYTICS_TOOL_QUERY_FAILED,
                error="invalid_group_by",
                group_by=group_by,
            )
            return ToolExecutionResult(
                content=(
                    f"Invalid group_by: {group_by!r}. "
                    f"Must be one of: {sorted(_VALID_GROUP_BY)}"
                ),
                is_error=True,
            )

        return None

    async def execute(  # noqa: PLR0911
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Query analytics data.

        Args:
            arguments: Must contain ``metrics`` and ``period``;
                optionally ``group_by``, ``start_date``, ``end_date``.

        Returns:
            A ``ToolExecutionResult`` with aggregated data.
        """
        if self._provider is None:
            logger.warning(
                ANALYTICS_TOOL_PROVIDER_NOT_CONFIGURED,
                tool="data_aggregator",
            )
            return ToolExecutionResult(
                content=(
                    "Analytics queries require a configured provider. "
                    "No AnalyticsProvider has been injected."
                ),
                is_error=True,
            )

        metrics = arguments.get("metrics")
        period = arguments.get("period")
        if not isinstance(metrics, list) or not metrics:
            logger.warning(
                ANALYTICS_TOOL_QUERY_FAILED,
                error="missing_or_invalid_metrics",
            )
            return ToolExecutionResult(
                content="'metrics' must be a non-empty list of strings.",
                is_error=True,
            )
        if not isinstance(period, str) or not period:
            logger.warning(
                ANALYTICS_TOOL_QUERY_FAILED,
                error="missing_or_invalid_period",
            )
            return ToolExecutionResult(
                content="'period' must be a non-empty string.",
                is_error=True,
            )
        group_by: str | None = arguments.get("group_by")
        start_date: str | None = arguments.get("start_date")
        end_date: str | None = arguments.get("end_date")

        error = self._validate_query_params(
            metrics,
            period,
            group_by,
            start_date,
            end_date,
        )
        if error is not None:
            return error

        logger.info(
            ANALYTICS_TOOL_QUERY_START,
            metrics=metrics,
            period=period,
            group_by=group_by,
        )

        try:
            data = await asyncio.wait_for(
                self._provider.query(
                    metrics=metrics,
                    period=period,
                    group_by=group_by,
                    start_date=start_date,
                    end_date=end_date,
                ),
                timeout=self._config.query_timeout,
            )
        except TimeoutError:
            logger.warning(
                ANALYTICS_TOOL_QUERY_FAILED,
                error="query_timeout",
                timeout=self._config.query_timeout,
                metrics=metrics,
            )
            return ToolExecutionResult(
                content=(
                    f"Analytics query timed out after {self._config.query_timeout}s"
                ),
                is_error=True,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                ANALYTICS_TOOL_QUERY_FAILED,
                error="provider_error",
                metrics=metrics,
                period=period,
                exc_info=True,
            )
            return ToolExecutionResult(
                content="Analytics query failed.",
                is_error=True,
            )

        # Enforce max_rows without mutating the provider's result.
        sanitized = {
            k: (
                v[: self._config.max_rows]
                if isinstance(v, list) and len(v) > self._config.max_rows
                else v
            )
            for k, v in data.items()
        }

        logger.info(
            ANALYTICS_TOOL_QUERY_SUCCESS,
            metrics=metrics,
            result_keys=sorted(sanitized.keys()),
        )

        # Format results as readable text
        lines = [f"Analytics query results ({period}):"]
        for key, value in sorted(sanitized.items()):
            lines.append(f"  {key}: {value}")

        return ToolExecutionResult(
            content="\n".join(lines),
            metadata=sanitized,
        )
