"""Tests for classification result models."""

from datetime import UTC, datetime

import pytest

from ai_company.budget.coordination_config import ErrorCategory
from ai_company.engine.classification.models import (
    ClassificationResult,
    ErrorFinding,
    ErrorSeverity,
)


@pytest.mark.unit
class TestErrorSeverity:
    """ErrorSeverity enum."""

    def test_values(self) -> None:
        assert ErrorSeverity.LOW.value == "low"
        assert ErrorSeverity.MEDIUM.value == "medium"
        assert ErrorSeverity.HIGH.value == "high"

    def test_member_count(self) -> None:
        assert len(ErrorSeverity) == 3


@pytest.mark.unit
class TestErrorFinding:
    """ErrorFinding model."""

    def test_construction(self) -> None:
        finding = ErrorFinding(
            category=ErrorCategory.LOGICAL_CONTRADICTION,
            severity=ErrorSeverity.HIGH,
            description="Contradictory statements detected",
        )
        assert finding.category == ErrorCategory.LOGICAL_CONTRADICTION
        assert finding.severity == ErrorSeverity.HIGH
        assert finding.description == "Contradictory statements detected"
        assert finding.evidence == ()
        assert finding.turn_range is None

    def test_frozen(self) -> None:
        finding = ErrorFinding(
            category=ErrorCategory.NUMERICAL_DRIFT,
            severity=ErrorSeverity.MEDIUM,
            description="Number drifted beyond threshold",
        )
        with pytest.raises(Exception):  # noqa: B017, PT011
            finding.severity = ErrorSeverity.LOW  # type: ignore[misc]

    def test_all_fields(self) -> None:
        finding = ErrorFinding(
            category=ErrorCategory.CONTEXT_OMISSION,
            severity=ErrorSeverity.LOW,
            description="Entity dropped from later turns",
            evidence=("Turn 1: 'AuthService'", "Turn 5: no mention"),
            turn_range=(1, 5),
        )
        assert finding.evidence == (
            "Turn 1: 'AuthService'",
            "Turn 5: no mention",
        )
        assert finding.turn_range == (1, 5)

    def test_evidence_as_tuple(self) -> None:
        finding = ErrorFinding(
            category=ErrorCategory.COORDINATION_FAILURE,
            severity=ErrorSeverity.HIGH,
            description="Tool execution error",
            evidence=("error: command not found",),
        )
        assert isinstance(finding.evidence, tuple)
        assert len(finding.evidence) == 1


@pytest.mark.unit
class TestClassificationResult:
    """ClassificationResult model."""

    def test_construction(self) -> None:
        result = ClassificationResult(
            execution_id="exec-001",
            agent_id="agent-1",
            task_id="task-1",
            categories_checked=(ErrorCategory.LOGICAL_CONTRADICTION,),
        )
        assert result.execution_id == "exec-001"
        assert result.agent_id == "agent-1"
        assert result.task_id == "task-1"
        assert result.categories_checked == (ErrorCategory.LOGICAL_CONTRADICTION,)
        assert result.findings == ()

    def test_empty_findings_computed_fields(self) -> None:
        result = ClassificationResult(
            execution_id="exec-002",
            agent_id="agent-1",
            task_id="task-1",
            categories_checked=tuple(ErrorCategory),
        )
        assert result.finding_count == 0
        assert result.has_findings is False

    def test_multiple_findings(self) -> None:
        findings = (
            ErrorFinding(
                category=ErrorCategory.LOGICAL_CONTRADICTION,
                severity=ErrorSeverity.HIGH,
                description="Contradiction found",
            ),
            ErrorFinding(
                category=ErrorCategory.NUMERICAL_DRIFT,
                severity=ErrorSeverity.MEDIUM,
                description="Number drifted",
            ),
        )
        result = ClassificationResult(
            execution_id="exec-003",
            agent_id="agent-1",
            task_id="task-1",
            categories_checked=tuple(ErrorCategory),
            findings=findings,
        )
        assert result.finding_count == 2
        assert result.has_findings is True

    def test_classified_at_defaults_to_now(self) -> None:
        before = datetime.now(UTC)
        result = ClassificationResult(
            execution_id="exec-004",
            agent_id="agent-1",
            task_id="task-1",
            categories_checked=(),
        )
        after = datetime.now(UTC)
        assert before <= result.classified_at <= after

    def test_frozen(self) -> None:
        result = ClassificationResult(
            execution_id="exec-005",
            agent_id="agent-1",
            task_id="task-1",
            categories_checked=(),
        )
        with pytest.raises(Exception):  # noqa: B017, PT011
            result.findings = ()  # type: ignore[misc]
