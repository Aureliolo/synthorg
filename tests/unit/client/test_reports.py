"""Unit tests for report strategy implementations."""

import json

import pytest

from synthorg.client.models import SimulationMetrics
from synthorg.client.protocols import ReportStrategy
from synthorg.client.report import (
    DetailedReport,
    JsonExportReport,
    MetricsOnlyReport,
    SummaryReport,
)

pytestmark = pytest.mark.unit


def _metrics() -> SimulationMetrics:
    return SimulationMetrics(
        total_requirements=20,
        total_tasks_created=15,
        tasks_accepted=10,
        tasks_rejected=3,
        tasks_reworked=2,
        avg_review_rounds=1.5,
        round_metrics=(
            {
                "round_number": 1,
                "total_requirements": 10,
                "tasks_created": 8,
                "accepted": 5,
                "rejected": 3,
            },
            {
                "round_number": 2,
                "total_requirements": 10,
                "tasks_created": 7,
                "accepted": 5,
                "rejected": 0,
            },
        ),
    )


class TestSummaryReport:
    def test_protocol_compatible(self) -> None:
        assert isinstance(SummaryReport(), ReportStrategy)

    async def test_summary_structure(self) -> None:
        report = await SummaryReport().generate_report(_metrics())
        assert report["format"] == "summary"
        totals = report["totals"]
        assert isinstance(totals, dict)
        assert totals["requirements"] == 20
        rates = report["rates"]
        assert isinstance(rates, dict)
        assert rates["acceptance_rate"] == pytest.approx(10 / 15)

    async def test_summary_has_no_per_round_detail(self) -> None:
        report = await SummaryReport().generate_report(_metrics())
        assert "per_round" not in report


class TestDetailedReport:
    def test_protocol_compatible(self) -> None:
        assert isinstance(DetailedReport(), ReportStrategy)

    async def test_detailed_includes_per_round(self) -> None:
        report = await DetailedReport().generate_report(_metrics())
        assert report["format"] == "detailed"
        assert "summary" in report
        per_round = report["per_round"]
        assert isinstance(per_round, list)
        assert len(per_round) == 2
        first = per_round[0]
        assert isinstance(first, dict)
        assert first["round_number"] == 1

    async def test_summary_narrative_text(self) -> None:
        report = await DetailedReport().generate_report(_metrics())
        summary = report["summary"]
        assert isinstance(summary, str)
        assert "20 requirements" in summary
        assert "%" in summary


class TestMetricsOnlyReport:
    def test_protocol_compatible(self) -> None:
        assert isinstance(MetricsOnlyReport(), ReportStrategy)

    async def test_returns_raw_model_dump(self) -> None:
        report = await MetricsOnlyReport().generate_report(_metrics())
        assert report["total_requirements"] == 20
        assert report["total_tasks_created"] == 15
        # Computed fields are included in model_dump.
        assert "acceptance_rate" in report


class TestJsonExportReport:
    def test_protocol_compatible(self) -> None:
        assert isinstance(JsonExportReport(), ReportStrategy)

    async def test_envelope_structure(self) -> None:
        report = await JsonExportReport().generate_report(_metrics())
        assert report["format"] == "json_export"
        assert "schema_version" in report
        assert "exported_at" in report
        metrics = report["metrics"]
        assert isinstance(metrics, dict)
        assert metrics["total_requirements"] == 20

    async def test_json_serializable(self) -> None:
        report = await JsonExportReport().generate_report(_metrics())
        encoded = json.dumps(report, default=str)
        assert "total_requirements" in encoded
