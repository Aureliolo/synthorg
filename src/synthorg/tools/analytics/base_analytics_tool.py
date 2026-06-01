"""Base class for analytics tools.

Provides the common ``ToolCategory.ANALYTICS`` category, a
shared configuration reference, and a metric-name validation
helper.
"""

from abc import ABC

from pydantic import JsonValue

from synthorg.core.enums import ToolCategory
from synthorg.tools.analytics.config import AnalyticsToolsConfig
from synthorg.tools.base import BaseTool


class BaseAnalyticsTool(BaseTool, ABC):
    """Abstract base for all analytics tools.

    Sets ``category=ToolCategory.ANALYTICS`` and holds a shared
    ``AnalyticsToolsConfig``.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str = "",
        parameters_schema: dict[str, JsonValue] | None = None,
        action_type: str | None = None,
        config: AnalyticsToolsConfig | None = None,
    ) -> None:
        """Initialize an analytics tool with configuration.

        Args:
            name: Tool name.
            description: Human-readable description.
            parameters_schema: JSON Schema for tool parameters.
            action_type: Security action type override.
            config: Analytics tool configuration.
        """
        super().__init__(
            name=name,
            description=description,
            category=ToolCategory.ANALYTICS,
            parameters_schema=parameters_schema,
            action_type=action_type,
        )
        self._config = config or AnalyticsToolsConfig()

    @property
    def config(self) -> AnalyticsToolsConfig:
        """The analytics tool configuration."""
        return self._config

    def _is_metric_allowed(self, metric_name: str) -> bool:
        """Check if a metric name is allowed by the whitelist.

        Args:
            metric_name: Name of the metric to check.

        Returns:
            ``True`` if the metric is allowed (or no whitelist
            is configured).
        """
        if self._config.allowed_metrics is None:
            return True
        return metric_name in self._config.allowed_metrics
