"""Metric collector tool -- record custom metrics via a sink.

The ``MetricSink`` protocol defines a vendor-agnostic interface
for recording metrics.  No concrete implementation is shipped;
users inject a sink at construction time.
"""

import math
from typing import ClassVar, Protocol, cast, override, runtime_checkable

from pydantic import BaseModel, JsonValue

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.analytics import (
    ANALYTICS_TOOL_METRIC_NOT_ALLOWED,
    ANALYTICS_TOOL_METRIC_RECORD_FAILED,
    ANALYTICS_TOOL_METRIC_RECORDED,
)
from synthorg.tools.analytics._args import MetricCollectorArgs
from synthorg.tools.analytics.base_analytics_tool import BaseAnalyticsTool
from synthorg.tools.analytics.config import AnalyticsToolsConfig
from synthorg.tools.base import ToolExecutionResult

logger = get_logger(__name__)


@runtime_checkable
class MetricSink(Protocol):
    """Abstracted metric recording sink protocol.

    Implementations must be async and accept individual
    metric data points.
    """

    async def record(
        self,
        *,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
        unit: str | None = None,
    ) -> None:
        """Record a metric data point.

        Args:
            name: Metric name.
            value: Metric value.
            tags: Optional key-value tags for the data point.
            unit: Optional measurement unit.
        """
        ...


class MetricCollectorTool(BaseAnalyticsTool):
    """Record custom metrics via an abstracted sink.

    Allows agents to record observations and measurements
    that are forwarded to the configured metric backend.

    Examples:
        Record a metric::

            tool = MetricCollectorTool(sink=my_sink)
            result = await tool.execute(
                arguments={
                    "metric_name": "response_time",
                    "value": 1.23,
                    "unit": "seconds",
                    "tags": {"endpoint": "/api/tasks"},
                }
            )
    """

    args_model: ClassVar[type[BaseModel] | None] = MetricCollectorArgs

    def __init__(
        self,
        *,
        sink: MetricSink | None = None,
        config: AnalyticsToolsConfig | None = None,
    ) -> None:
        """Initialize the metric collector tool.

        Args:
            sink: Metric backend that records counters / gauges /
                timings. ``None`` makes ``execute`` return a
                configuration error.
            config: Analytics tool configuration with metric-name
                allowlist. ``None`` falls back to defaults.
        """
        super().__init__(
            name="metric_collector",
            description=(
                "Record custom metrics (counters, gauges, timings) "
                "to the configured metric backend."
            ),
            parameters_schema=MetricCollectorArgs.model_json_schema(),
            action_type="metrics:record",
            config=config,
        )
        self._sink = sink

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Record a metric data point.

        Args:
            arguments: Must contain ``metric_name`` and ``value``;
                optionally ``tags`` and ``unit``.

        Returns:
            A ``ToolExecutionResult`` confirming the recording.
        """
        if self._sink is None:
            logger.warning(
                ANALYTICS_TOOL_METRIC_RECORD_FAILED,
                metric_name=arguments.get("metric_name", "unknown"),
                error="sink_not_configured",
            )
            return ToolExecutionResult(
                content=(
                    "Metric recording requires a configured sink. "
                    "No MetricSink has been injected."
                ),
                is_error=True,
            )

        metric_name = arguments.get("metric_name")
        value = arguments.get("value")
        if not isinstance(metric_name, str) or not metric_name.strip():
            logger.warning(
                ANALYTICS_TOOL_METRIC_RECORD_FAILED,
                error="missing_or_invalid_metric_name",
            )
            return ToolExecutionResult(
                content="'metric_name' must be a non-empty string.",
                is_error=True,
            )
        if isinstance(value, bool) or not isinstance(value, int | float):
            logger.warning(
                ANALYTICS_TOOL_METRIC_RECORD_FAILED,
                error="invalid_value_type",
            )
            return ToolExecutionResult(
                content="'value' must be a number (not bool).",
                is_error=True,
            )
        value = float(value)
        raw_tags = arguments.get("tags")
        tags: dict[str, str] = (
            cast("dict[str, str]", raw_tags) if isinstance(raw_tags, dict) else {}
        )
        unit = arguments.get("unit")
        if unit is not None and not isinstance(unit, str):
            logger.warning(
                ANALYTICS_TOOL_METRIC_RECORD_FAILED,
                error="invalid_unit_type",
            )
            return ToolExecutionResult(
                content="'unit' must be a string or null.",
                is_error=True,
            )

        if not self._is_metric_allowed(metric_name):
            logger.warning(
                ANALYTICS_TOOL_METRIC_NOT_ALLOWED,
                metric_name=metric_name,
            )
            return ToolExecutionResult(
                content=(
                    f"Metric not allowed: {metric_name!r}. "
                    f"Allowed: {sorted(self._config.allowed_metrics or set())}"
                ),
                is_error=True,
            )

        if not math.isfinite(value):
            logger.warning(
                ANALYTICS_TOOL_METRIC_RECORD_FAILED,
                metric_name=metric_name,
                error="non_finite_value",
                value=str(value),
            )
            return ToolExecutionResult(
                content=(f"Metric value must be finite: {metric_name!r} got {value}"),
                is_error=True,
            )

        try:
            await self._sink.record(
                name=metric_name,
                value=value,
                tags=tags,
                unit=unit,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                ANALYTICS_TOOL_METRIC_RECORD_FAILED,
                metric_name=metric_name,
                error="sink_error",
            )
            return ToolExecutionResult(
                content="Metric recording failed.",
                is_error=True,
            )

        logger.info(
            ANALYTICS_TOOL_METRIC_RECORDED,
            metric_name=metric_name,
            value=value,
            unit=unit,
        )

        unit_suffix = f" {unit}" if unit else ""
        tag_metadata = cast("dict[str, JsonValue]", dict(tags))
        return ToolExecutionResult(
            content=(f"Metric recorded: {metric_name} = {value}{unit_suffix}"),
            metadata={
                "metric_name": metric_name,
                "value": value,
                "tags": tag_metadata,
                "unit": unit,
            },
        )
