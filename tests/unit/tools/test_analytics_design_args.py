"""Tests for typed analytics + design tool argument models."""

import pytest
from pydantic import ValidationError

from synthorg.tools.analytics._args import (
    DataAggregatorArgs,
    MetricCollectorArgs,
    ReportGeneratorArgs,
)
from synthorg.tools.design._args import (
    AssetManagerArgs,
    DiagramGeneratorArgs,
    ImageGeneratorArgs,
)

# ── Analytics ──────────────────────────────────────────────────────


class TestDataAggregatorArgs:
    @pytest.mark.unit
    def test_minimal(self) -> None:
        args = DataAggregatorArgs(metrics=("total_cost",), period="7d")
        assert args.group_by is None

    @pytest.mark.unit
    def test_custom_period_requires_dates(self) -> None:
        with pytest.raises(ValidationError, match="custom"):
            DataAggregatorArgs(metrics=("x",), period="custom")
        with pytest.raises(ValidationError, match="custom"):
            DataAggregatorArgs(
                metrics=("x",),
                period="custom",
                start_date="2026-01-01",
            )

    @pytest.mark.unit
    def test_custom_period_with_dates_succeeds(self) -> None:
        args = DataAggregatorArgs(
            metrics=("x",),
            period="custom",
            start_date="2026-01-01",
            end_date="2026-02-01",
        )
        assert args.start_date == "2026-01-01"

    @pytest.mark.unit
    def test_invalid_period_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DataAggregatorArgs.model_validate(
                {"metrics": ["x"], "period": "1y"},
            )

    @pytest.mark.unit
    def test_empty_metrics_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DataAggregatorArgs(metrics=(), period="7d")


class TestMetricCollectorArgs:
    @pytest.mark.unit
    def test_construction(self) -> None:
        args = MetricCollectorArgs(metric_name="response_time", value=1.23)
        assert args.tags == {}

    @pytest.mark.unit
    def test_with_tags_and_unit(self) -> None:
        args = MetricCollectorArgs(
            metric_name="x",
            value=1.0,
            tags={"endpoint": "/api"},
            unit="seconds",
        )
        assert args.tags == {"endpoint": "/api"}
        assert args.unit == "seconds"


class TestReportGeneratorArgs:
    @pytest.mark.unit
    def test_default_format(self) -> None:
        args = ReportGeneratorArgs(report_type="budget_summary", period="7d")
        assert args.format == "markdown"

    @pytest.mark.unit
    def test_invalid_report_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReportGeneratorArgs.model_validate(
                {"report_type": "exec_summary", "period": "7d"},
            )


# ── Design ─────────────────────────────────────────────────────────


class TestImageGeneratorArgs:
    @pytest.mark.unit
    def test_defaults(self) -> None:
        args = ImageGeneratorArgs(prompt="sunset")
        assert args.width == 1024
        assert args.height == 1024
        assert args.style == "realistic"
        assert args.quality == "standard"

    @pytest.mark.unit
    def test_dimension_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ImageGeneratorArgs(prompt="x", width=255)
        with pytest.raises(ValidationError):
            ImageGeneratorArgs(prompt="x", height=2049)


class TestDiagramGeneratorArgs:
    @pytest.mark.unit
    def test_construction(self) -> None:
        args = DiagramGeneratorArgs(
            diagram_type="flowchart",
            description="A -> B -> C",
        )
        assert args.title == ""
        assert args.output_format == "mermaid"

    @pytest.mark.unit
    def test_invalid_diagram_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DiagramGeneratorArgs.model_validate(
                {"diagram_type": "mind_map", "description": "x"},
            )


class TestAssetManagerArgs:
    @pytest.mark.unit
    def test_list_action(self) -> None:
        args = AssetManagerArgs(action="list")
        assert args.asset_id is None
        assert args.tags == ()

    @pytest.mark.unit
    def test_get_with_asset_id(self) -> None:
        args = AssetManagerArgs(action="get", asset_id="img-1")
        assert args.asset_id == "img-1"
