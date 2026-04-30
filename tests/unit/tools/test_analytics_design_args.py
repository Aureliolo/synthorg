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
    @pytest.mark.parametrize(
        "extra_dates",
        [
            pytest.param({}, id="missing_both"),
            pytest.param({"start_date": "2026-01-01"}, id="missing_end"),
            pytest.param({"end_date": "2026-02-01"}, id="missing_start"),
        ],
    )
    def test_custom_period_requires_dates(self, extra_dates: dict[str, str]) -> None:
        """``period='custom'`` requires both ``start_date`` and ``end_date``."""
        payload: dict[str, object] = {
            "metrics": ["x"],
            "period": "custom",
            **extra_dates,
        }
        with pytest.raises(ValidationError, match="custom"):
            DataAggregatorArgs.model_validate(payload)

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
    def test_custom_period_rejects_inverted_range(self) -> None:
        """``start_date > end_date`` fails at the typed boundary."""
        with pytest.raises(ValidationError, match="on or before"):
            DataAggregatorArgs(
                metrics=("x",),
                period="custom",
                start_date="2026-02-01",
                end_date="2026-01-01",
            )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "extra_dates",
        [
            pytest.param({"start_date": "2026-01-01"}, id="start_only"),
            pytest.param({"end_date": "2026-02-01"}, id="end_only"),
            pytest.param(
                {"start_date": "2026-01-01", "end_date": "2026-02-01"},
                id="both",
            ),
        ],
    )
    def test_non_custom_period_rejects_dates(self, extra_dates: dict[str, str]) -> None:
        """``start_date`` / ``end_date`` are only allowed with ``period='custom'``."""
        payload: dict[str, object] = {
            "metrics": ["x"],
            "period": "7d",
            **extra_dates,
        }
        with pytest.raises(ValidationError, match="only allowed when period='custom'"):
            DataAggregatorArgs.model_validate(payload)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "bad_date",
        # Non-blank but non-ISO-8601 strings.  Whitespace-only inputs
        # are rejected one layer earlier by ``NotBlankStr``; this
        # parametrize exercises the ``IsoDateStr`` AfterValidator
        # specifically.
        ["not-a-date", "2026/01/01", "tomorrow", "01-01-2026"],
    )
    def test_custom_period_rejects_invalid_date_format(self, bad_date: str) -> None:
        """Non-ISO date strings fail at the ``IsoDateStr`` boundary."""
        with pytest.raises(ValidationError, match="valid ISO 8601 date"):
            DataAggregatorArgs.model_validate(
                {
                    "metrics": ["x"],
                    "period": "custom",
                    "start_date": bad_date,
                    "end_date": "2026-02-01",
                },
            )

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
    @pytest.mark.parametrize(
        "dimension",
        [
            pytest.param({"width": 255}, id="width_below_min"),
            pytest.param({"height": 2049}, id="height_above_max"),
        ],
    )
    def test_dimension_bounds(self, dimension: dict[str, int]) -> None:
        """Dimensions outside the [256, 2048] range are rejected."""
        payload: dict[str, object] = {"prompt": "x", **dimension}
        with pytest.raises(ValidationError):
            ImageGeneratorArgs.model_validate(payload)


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

    @pytest.mark.unit
    @pytest.mark.parametrize("action", ["get", "delete"])
    def test_get_or_delete_requires_asset_id(self, action: str) -> None:
        """``get`` and ``delete`` require ``asset_id`` at the boundary."""
        with pytest.raises(ValidationError):
            AssetManagerArgs.model_validate({"action": action})
