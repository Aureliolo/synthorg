"""Report generator tool -- produce formatted analytics reports.

Delegates data fetching to an ``AnalyticsProvider`` and formats
the results into human-readable reports in text, markdown, or JSON.
"""

import asyncio
import json
from typing import ClassVar, Final, override

from pydantic import BaseModel, JsonValue

from synthorg.core.boundary import parse_typed
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.analytics import (
    ANALYTICS_TOOL_PROVIDER_NOT_CONFIGURED,
    ANALYTICS_TOOL_REPORT_FAILED,
    ANALYTICS_TOOL_REPORT_START,
    ANALYTICS_TOOL_REPORT_SUCCESS,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.analytics._args import ReportGeneratorArgs
from synthorg.tools.analytics.base_analytics_tool import BaseAnalyticsTool
from synthorg.tools.analytics.config import AnalyticsToolsConfig
from synthorg.tools.analytics.data_aggregator import (
    AnalyticsProvider,
)
from synthorg.tools.base import ToolExecutionResult

logger = get_logger(__name__)

_REPORT_METRICS: Final[dict[str, list[str]]] = {
    "budget_summary": ["total_cost", "budget_remaining", "burn_rate"],
    "performance": [
        "task_completion_rate",
        "average_latency",
        "error_rate",
    ],
    "trend_analysis": ["total_cost", "task_count", "active_agents"],
    "cost_breakdown": [
        "cost_by_agent",
        "cost_by_department",
        "cost_by_model",
    ],
}


class ReportGeneratorTool(BaseAnalyticsTool):
    """Generate formatted analytics reports.

    Queries the analytics provider for relevant metrics and
    formats the results into a structured report.

    Examples:
        Generate a budget report::

            tool = ReportGeneratorTool(provider=my_provider)
            result = await tool.execute(
                arguments={
                    "report_type": "budget_summary",
                    "period": "30d",
                    "format": "markdown",
                }
            )
    """

    args_model: ClassVar[type[BaseModel] | None] = ReportGeneratorArgs

    def __init__(
        self,
        *,
        provider: AnalyticsProvider | None = None,
        config: AnalyticsToolsConfig | None = None,
    ) -> None:
        """Initialize the report generator tool.

        Args:
            provider: Analytics data source. ``None`` makes
                ``execute`` return a configuration error.
            config: Analytics tool configuration. ``None`` falls back
                to defaults.
        """
        super().__init__(
            name="report_generator",
            description=(
                "Generate formatted analytics reports "
                "(budget, performance, trends, cost breakdown)."
            ),
            parameters_schema=ReportGeneratorArgs.model_json_schema(),
            action_type=ActionType.CODE_READ,
            config=config,
        )
        self._provider = provider

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Generate an analytics report.

        Args:
            arguments: Must contain ``report_type`` and ``period``;
                optionally ``format``.

        Returns:
            A ``ToolExecutionResult`` with the formatted report.
        """
        if self._provider is None:
            logger.warning(
                ANALYTICS_TOOL_PROVIDER_NOT_CONFIGURED,
                tool="report_generator",
            )
            return ToolExecutionResult(
                content=(
                    "Report generation requires a configured provider. "
                    "No AnalyticsProvider has been injected."
                ),
                is_error=True,
            )

        # ``parse_typed`` validates ``report_type`` / ``period`` /
        # ``format`` against their Literal contracts, so an out-of-set
        # value is rejected at the boundary and the lookups below are
        # total.
        args = parse_typed("tool.report_generator", arguments, ReportGeneratorArgs)
        report_type = args.report_type
        period = args.period
        output_format = args.format

        metrics = list(_REPORT_METRICS.get(report_type, []))

        if self._config.allowed_metrics is not None:
            blocked = [m for m in metrics if m not in self._config.allowed_metrics]
            if blocked:
                logger.warning(
                    ANALYTICS_TOOL_REPORT_FAILED,
                    error="metrics_not_allowed",
                    report_type=report_type,
                    blocked_metrics=blocked,
                )
                return ToolExecutionResult(
                    content=(
                        f"Report type {report_type!r} requires metrics "
                        f"not in the allowed list: {blocked}"
                    ),
                    is_error=True,
                )

        logger.info(
            ANALYTICS_TOOL_REPORT_START,
            report_type=report_type,
            period=period,
            output_format=output_format,
        )

        try:
            data = await asyncio.wait_for(
                self._provider.query(
                    metrics=metrics,
                    period=period,
                ),
                timeout=self._config.query_timeout,
            )
        except TimeoutError:
            logger.warning(
                ANALYTICS_TOOL_REPORT_FAILED,
                error="query_timeout",
                timeout=self._config.query_timeout,
                report_type=report_type,
            )
            return ToolExecutionResult(
                content=(f"Report query timed out after {self._config.query_timeout}s"),
                is_error=True,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                ANALYTICS_TOOL_REPORT_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                report_type=report_type,
            )
            # The tool result is surfaced to the agent turn; raw
            # exception text on persistence/database errors can
            # carry connection strings. Use a stable generic
            # message; the scrubbed log above carries operator
            # detail.
            return ToolExecutionResult(
                content="Report generation failed",
                is_error=True,
            )

        try:
            report = self._format_report(report_type, period, data, output_format)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                ANALYTICS_TOOL_REPORT_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                report_type=report_type,
            )
            # Generic content (mirrors the query-failure branch);
            # ``ToolExecutionResult.content`` reaches the LLM so
            # ``exc`` text would leak past the log scrub above.
            return ToolExecutionResult(
                content="Report formatting failed",
                is_error=True,
            )

        logger.info(
            ANALYTICS_TOOL_REPORT_SUCCESS,
            report_type=report_type,
            output_length=len(report),
        )

        return ToolExecutionResult(
            content=report,
            metadata={
                "report_type": report_type,
                "period": period,
                "format": output_format,
            },
        )

    @staticmethod
    def _format_report(
        report_type: str,
        period: str,
        data: dict[str, JsonValue],
        output_format: str,
    ) -> str:
        """Format report data into the requested output format.

        Args:
            report_type: Type of report.
            period: Reporting period.
            data: Raw data from the analytics provider.
            output_format: Desired output format.

        Returns:
            Formatted report string.
        """
        if output_format == "json":
            return json.dumps(
                {
                    "report_type": report_type,
                    "period": period,
                    "data": data,
                },
                indent=2,
                default=str,
            )

        title = report_type.replace("_", " ").title()

        if output_format == "markdown":
            lines = [f"# {title} Report", "", f"**Period:** {period}", ""]
            for key, value in sorted(data.items()):
                lines.append(f"- **{key}:** {value}")
            return "\n".join(lines)

        # Plain text
        lines = [f"{title} Report", f"Period: {period}", ""]
        for key, value in sorted(data.items()):
            lines.append(f"  {key}: {value}")
        return "\n".join(lines)
