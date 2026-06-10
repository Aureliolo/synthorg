"""Tests for external benchmark models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.execution.turn import BehaviorTag
from synthorg.hr.evaluation.external_benchmark_models import (
    BenchmarkGrade,
    BenchmarkRef,
    BenchmarkRunResult,
    EvalCycleReport,
    EvalDataset,
    EvalTestCase,
)
from tests._shared import as_uuid


@pytest.mark.unit
class TestEvalTestCase:
    """EvalTestCase frozen model validation."""

    def test_valid_construction(self) -> None:
        case = EvalTestCase(
            id="case-1",
            behavior_tags=(BehaviorTag.FILE_OPERATIONS,),
            input_data="test input",
            expected_output="test output",
        )
        assert case.id == "case-1"
        assert case.metadata == {}

    def test_metadata_deepcopy(self) -> None:
        original = {"key": ["value"]}
        case = EvalTestCase(
            id="case-1",
            behavior_tags=(BehaviorTag.RETRIEVAL,),
            input_data="test",
            expected_output="test",
            metadata=original,
        )
        # Mutating original should not affect the model.
        original["key"].append("mutated")
        assert case.metadata == {"key": ["value"]}


@pytest.mark.unit
class TestBenchmarkGrade:
    """BenchmarkGrade frozen model validation."""

    def test_valid_construction(self) -> None:
        grade = BenchmarkGrade(
            passed=True,
            score=0.95,
            explanation="Good output",
        )
        assert grade.passed is True
        assert grade.score == 0.95

    def test_score_bounds(self) -> None:
        with pytest.raises(ValidationError):
            BenchmarkGrade(passed=True, score=1.5, explanation="bad")
        with pytest.raises(ValidationError):
            BenchmarkGrade(passed=True, score=-0.1, explanation="bad")


@pytest.mark.unit
class TestBenchmarkRunResult:
    """BenchmarkRunResult frozen model validation."""

    def test_valid_construction(self) -> None:
        result = BenchmarkRunResult(
            benchmark_name="test-bench",
            cases_run=10,
            passed_count=8,
            average_score=0.8,
            completed_at=datetime.now(UTC),
        )
        assert result.cases_run == 10
        assert result.passed_count == 8


@pytest.mark.unit
class TestEvalDataset:
    """EvalDataset frozen model validation."""

    def test_valid_construction(self) -> None:
        case = EvalTestCase(
            id="case-1",
            behavior_tags=(BehaviorTag.TOOL_USE,),
            input_data="test",
            expected_output="test",
        )
        dataset = EvalDataset(
            name="test-dataset",
            source="hand_written",
            behavior_tags=(BehaviorTag.TOOL_USE,),
            test_cases=(case,),
            created_at=datetime.now(UTC),
            version="v1",
        )
        assert dataset.name == "test-dataset"
        assert len(dataset.test_cases) == 1


@pytest.mark.unit
class TestBenchmarkRef:
    """BenchmarkRef frozen model validation."""

    def test_defaults(self) -> None:
        ref = BenchmarkRef(name="test-bench")
        assert ref.enabled is True

    def test_disabled(self) -> None:
        ref = BenchmarkRef(name="test-bench", enabled=False)
        assert ref.enabled is False


@pytest.mark.unit
class TestEvalCycleReport:
    """EvalCycleReport frozen model validation."""

    def test_valid_construction(self) -> None:
        report = EvalCycleReport(
            cycle_id=as_uuid("cycle-1"),
            window_start=datetime(2026, 1, 1, tzinfo=UTC),
            window_end=datetime(2026, 1, 2, tzinfo=UTC),
            duration_seconds=10.5,
            agents_evaluated=3,
            created_at=datetime.now(UTC),
        )
        assert report.agents_evaluated == 3
        assert report.observations == ()
        assert report.proposed_actions == ()
        assert report.training_triggered is False
        assert report.benchmark_results == ()
