"""Tests for the metric collector tool."""

from typing import Any, cast

import pytest

from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.analytics.config import AnalyticsToolsConfig
from synthorg.tools.analytics.metric_collector import (
    MetricCollectorTool,
    MetricSink,
)

from .conftest import MockMetricSink


@pytest.mark.unit
class TestMetricCollectorTool:
    """Tests for MetricCollectorTool."""

    @pytest.mark.parametrize(
        ("attr", "expected"),
        [
            ("category", ToolCategory.ANALYTICS),
            ("action_type", "metrics:record"),
            ("name", "metric_collector"),
        ],
        ids=["category", "action_type", "name"],
    )
    def test_tool_attributes(
        self, mock_sink: MockMetricSink, attr: str, expected: object
    ) -> None:
        tool = MetricCollectorTool(sink=mock_sink)
        assert getattr(tool, attr) == expected

    async def test_execute_no_sink_returns_error(self) -> None:
        tool = MetricCollectorTool(sink=None)
        result = await tool.execute(
            arguments={
                "metric_name": "test",
                "value": 1.0,
            }
        )
        assert result.is_error
        assert "No MetricSink" in result.content

    async def test_execute_success(
        self,
        mock_sink: MockMetricSink,
    ) -> None:
        tool = MetricCollectorTool(sink=mock_sink)
        result = await tool.execute(
            arguments={
                "metric_name": "response_time",
                "value": 1.23,
                "unit": "seconds",
            }
        )
        assert not result.is_error
        assert "response_time" in result.content
        assert "1.23" in result.content
        assert "seconds" in result.content
        assert len(mock_sink.recorded) == 1
        recorded = mock_sink.recorded[0]
        assert recorded["name"] == "response_time"
        assert recorded["value"] == 1.23

    async def test_execute_with_tags(
        self,
        mock_sink: MockMetricSink,
    ) -> None:
        tool = MetricCollectorTool(sink=mock_sink)
        result = await tool.execute(
            arguments={
                "metric_name": "request_count",
                "value": 42,
                "tags": {"endpoint": "/api/tasks"},
            }
        )
        assert not result.is_error
        assert len(mock_sink.recorded) == 1
        recorded = mock_sink.recorded[0]
        assert recorded["tags"]["endpoint"] == "/api/tasks"

    async def test_execute_metric_not_allowed(
        self,
        mock_sink: MockMetricSink,
        restricted_config: AnalyticsToolsConfig,
    ) -> None:
        tool = MetricCollectorTool(
            sink=mock_sink,
            config=restricted_config,
        )
        result = await tool.execute(
            arguments={
                "metric_name": "secret_metric",
                "value": 1.0,
            }
        )
        assert result.is_error
        assert "not allowed" in result.content

    async def test_execute_metric_allowed(
        self,
        mock_sink: MockMetricSink,
        restricted_config: AnalyticsToolsConfig,
    ) -> None:
        tool = MetricCollectorTool(
            sink=mock_sink,
            config=restricted_config,
        )
        result = await tool.execute(
            arguments={
                "metric_name": "total_cost",
                "value": 100.0,
            }
        )
        assert not result.is_error

    async def test_execute_sink_error(
        self,
        failing_sink: MockMetricSink,
    ) -> None:
        tool = MetricCollectorTool(sink=failing_sink)
        result = await tool.execute(
            arguments={
                "metric_name": "test",
                "value": 1.0,
            }
        )
        assert result.is_error
        assert "Metric recording failed" in result.content

    async def test_execute_returns_metadata(
        self,
        mock_sink: MockMetricSink,
    ) -> None:
        tool = MetricCollectorTool(sink=mock_sink)
        result = await tool.execute(
            arguments={
                "metric_name": "cpu_usage",
                "value": 85.5,
                "unit": "percent",
                "tags": {"host": "worker-1"},
            }
        )
        assert not result.is_error
        assert result.metadata["metric_name"] == "cpu_usage"
        assert result.metadata["value"] == 85.5
        assert result.metadata["unit"] == "percent"
        assert cast("dict[str, Any]", result.metadata)["tags"]["host"] == "worker-1"

    def test_mock_sink_satisfies_protocol(
        self,
        mock_sink: MockMetricSink,
    ) -> None:
        assert isinstance(mock_sink, MetricSink)

    def test_parameters_schema_requires_name_and_value(
        self,
        mock_sink: MockMetricSink,
    ) -> None:
        tool = MetricCollectorTool(sink=mock_sink)
        schema = tool.parameters_schema
        assert schema is not None
        required = cast("dict[str, Any]", schema)["required"]
        assert "metric_name" in required
        assert "value" in required
