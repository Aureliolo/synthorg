"""Tests for the data aggregator tool."""

from typing import Any, cast

import pytest

from synthorg.core.enums import ActionType, ToolCategory
from synthorg.tools.analytics.config import AnalyticsToolsConfig
from synthorg.tools.analytics.data_aggregator import (
    AnalyticsProvider,
    DataAggregatorTool,
)

from .conftest import MockAnalyticsProvider


@pytest.mark.unit
class TestDataAggregatorTool:
    """Tests for DataAggregatorTool."""

    @pytest.mark.parametrize(
        ("attr", "expected"),
        [
            ("category", ToolCategory.ANALYTICS),
            ("action_type", ActionType.CODE_READ),
            ("name", "data_aggregator"),
        ],
        ids=["category", "action_type", "name"],
    )
    def test_tool_attributes(
        self,
        mock_provider: MockAnalyticsProvider,
        attr: str,
        expected: object,
    ) -> None:
        tool = DataAggregatorTool(provider=mock_provider)
        assert getattr(tool, attr) == expected

    async def test_execute_no_provider_returns_error(self) -> None:
        tool = DataAggregatorTool(provider=None)
        result = await tool.execute(
            arguments={
                "metrics": ["total_cost"],
                "period": "7d",
            }
        )
        assert result.is_error
        assert "No AnalyticsProvider" in result.content

    async def test_execute_success(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = DataAggregatorTool(provider=mock_provider)
        result = await tool.execute(
            arguments={
                "metrics": ["total_cost", "task_count"],
                "period": "7d",
            }
        )
        assert not result.is_error
        assert "total_cost" in result.content
        assert result.metadata["total_cost"] == 1234.56
        assert len(mock_provider.calls) == 1

    async def test_execute_passes_all_params(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = DataAggregatorTool(provider=mock_provider)
        await tool.execute(
            arguments={
                "metrics": ["total_cost"],
                "period": "custom",
                "group_by": "day",
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
            }
        )
        call = mock_provider.calls[0]
        assert call["metrics"] == ["total_cost"]
        assert call["period"] == "custom"
        assert call["group_by"] == "day"
        assert call["start_date"] == "2026-01-01"
        assert call["end_date"] == "2026-01-31"

    async def test_execute_invalid_period(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = DataAggregatorTool(provider=mock_provider)
        result = await tool.execute(
            arguments={
                "metrics": ["total_cost"],
                "period": "invalid",
            }
        )
        assert result.is_error
        assert "Invalid period" in result.content

    async def test_execute_invalid_group_by(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = DataAggregatorTool(provider=mock_provider)
        result = await tool.execute(
            arguments={
                "metrics": ["total_cost"],
                "period": "7d",
                "group_by": "invalid",
            }
        )
        assert result.is_error
        assert "Invalid group_by" in result.content

    async def test_execute_provider_error(
        self,
        failing_provider: MockAnalyticsProvider,
    ) -> None:
        tool = DataAggregatorTool(provider=failing_provider)
        result = await tool.execute(
            arguments={
                "metrics": ["total_cost"],
                "period": "7d",
            }
        )
        assert result.is_error
        assert "Analytics query failed" in result.content

    async def test_execute_metric_whitelist(
        self,
        mock_provider: MockAnalyticsProvider,
        restricted_config: AnalyticsToolsConfig,
    ) -> None:
        tool = DataAggregatorTool(
            provider=mock_provider,
            config=restricted_config,
        )
        result = await tool.execute(
            arguments={
                "metrics": ["total_cost", "secret_metric"],
                "period": "7d",
            }
        )
        assert result.is_error
        assert "not allowed" in result.content

    async def test_execute_metric_whitelist_allowed(
        self,
        mock_provider: MockAnalyticsProvider,
        restricted_config: AnalyticsToolsConfig,
    ) -> None:
        tool = DataAggregatorTool(
            provider=mock_provider,
            config=restricted_config,
        )
        result = await tool.execute(
            arguments={
                "metrics": ["total_cost"],
                "period": "7d",
            }
        )
        assert not result.is_error

    async def test_execute_custom_period_requires_dates(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = DataAggregatorTool(provider=mock_provider)
        result = await tool.execute(
            arguments={
                "metrics": ["total_cost"],
                "period": "custom",
            }
        )
        assert result.is_error
        assert "start_date" in result.content

    async def test_execute_invalid_date_format(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = DataAggregatorTool(provider=mock_provider)
        result = await tool.execute(
            arguments={
                "metrics": ["total_cost"],
                "period": "custom",
                "start_date": "not-a-date",
                "end_date": "2026-01-31",
            }
        )
        assert result.is_error
        assert "Invalid start_date" in result.content

    def test_mock_provider_satisfies_protocol(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        assert isinstance(mock_provider, AnalyticsProvider)

    def test_parameters_schema_requires_metrics_and_period(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = DataAggregatorTool(provider=mock_provider)
        schema = tool.parameters_schema
        assert schema is not None
        required = cast("dict[str, Any]", schema)["required"]
        assert "metrics" in required
        assert "period" in required
