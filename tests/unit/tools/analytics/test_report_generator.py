"""Tests for the report generator tool."""

import json

import pytest

from synthorg.core.enums import ActionType, ToolCategory
from synthorg.tools.analytics.report_generator import ReportGeneratorTool

from .conftest import MockAnalyticsProvider


@pytest.mark.unit
class TestReportGeneratorTool:
    """Tests for ReportGeneratorTool."""

    @pytest.mark.parametrize(
        ("attr", "expected"),
        [
            ("category", ToolCategory.ANALYTICS),
            ("action_type", ActionType.CODE_READ),
            ("name", "report_generator"),
        ],
        ids=["category", "action_type", "name"],
    )
    def test_tool_attributes(
        self, mock_provider: MockAnalyticsProvider, attr: str, expected: object
    ) -> None:
        tool = ReportGeneratorTool(provider=mock_provider)
        assert getattr(tool, attr) == expected

    async def test_execute_no_provider_returns_error(self) -> None:
        tool = ReportGeneratorTool(provider=None)
        result = await tool.execute(
            arguments={
                "report_type": "budget_summary",
                "period": "30d",
            }
        )
        assert result.is_error
        assert "No AnalyticsProvider" in result.content

    async def test_execute_markdown_report(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = ReportGeneratorTool(provider=mock_provider)
        result = await tool.execute(
            arguments={
                "report_type": "budget_summary",
                "period": "30d",
                "format": "markdown",
            }
        )
        assert not result.is_error
        assert "# Budget Summary Report" in result.content
        assert "**Period:** 30d" in result.content
        assert result.metadata["format"] == "markdown"

    async def test_execute_text_report(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = ReportGeneratorTool(provider=mock_provider)
        result = await tool.execute(
            arguments={
                "report_type": "performance",
                "period": "7d",
                "format": "text",
            }
        )
        assert not result.is_error
        assert "Performance Report" in result.content
        assert "Period: 7d" in result.content

    async def test_execute_json_report(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = ReportGeneratorTool(provider=mock_provider)
        result = await tool.execute(
            arguments={
                "report_type": "cost_breakdown",
                "period": "90d",
                "format": "json",
            }
        )
        assert not result.is_error
        parsed = json.loads(result.content)
        assert parsed["report_type"] == "cost_breakdown"
        assert parsed["period"] == "90d"
        assert "data" in parsed

    async def test_execute_default_format_is_markdown(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = ReportGeneratorTool(provider=mock_provider)
        result = await tool.execute(
            arguments={
                "report_type": "trend_analysis",
                "period": "7d",
            }
        )
        assert not result.is_error
        assert result.metadata["format"] == "markdown"

    async def test_execute_invalid_report_type(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = ReportGeneratorTool(provider=mock_provider)
        result = await tool.execute(
            arguments={
                "report_type": "invalid",
                "period": "7d",
            }
        )
        assert result.is_error
        assert "Invalid report_type" in result.content

    async def test_execute_invalid_format(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = ReportGeneratorTool(provider=mock_provider)
        result = await tool.execute(
            arguments={
                "report_type": "budget_summary",
                "period": "7d",
                "format": "csv",
            }
        )
        assert result.is_error
        assert "Invalid format" in result.content

    async def test_execute_provider_error(
        self,
        failing_provider: MockAnalyticsProvider,
    ) -> None:
        tool = ReportGeneratorTool(provider=failing_provider)
        result = await tool.execute(
            arguments={
                "report_type": "budget_summary",
                "period": "7d",
            }
        )
        assert result.is_error
        # SEC-1 (#1682): the tool result now surfaces a stable
        # generic message; raw exception text would leak DB
        # connection strings on real failures. Operators see the
        # scrubbed detail in the ANALYTICS_TOOL_REPORT_FAILED log.
        # Equality (not substring) so the gate fails if a future
        # change appends raw exception text after the prefix.
        assert result.content == "Report generation failed"

    async def test_execute_formatting_error(
        self,
        mock_provider: MockAnalyticsProvider,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Force ``_format_report`` to raise; assert the scrubbed branch.

        Pre-PR review #1682 (CodeRabbit at test_report_generator.py:155):
        ``test_execute_provider_error`` covers the provider-query
        failure path, but the formatter exception branch (different
        ``except`` block in ``execute()``) had no exact-match gate.
        A regression that re-introduced ``content=f"Report formatting
        failed: {exc}"`` would slip past the provider-error test.
        """
        tool = ReportGeneratorTool(provider=mock_provider)
        monkeypatch.setattr(
            tool,
            "_format_report",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("internal SQL: SELECT * FROM secrets"),
            ),
        )
        result = await tool.execute(
            arguments={
                "report_type": "budget_summary",
                "period": "7d",
            }
        )
        assert result.is_error
        assert result.content == "Report formatting failed"

    def test_parameters_schema_required_fields(
        self,
        mock_provider: MockAnalyticsProvider,
    ) -> None:
        tool = ReportGeneratorTool(provider=mock_provider)
        schema = tool.parameters_schema
        assert schema is not None
        assert "report_type" in schema["required"]
        assert "period" in schema["required"]
