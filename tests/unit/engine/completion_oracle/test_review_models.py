"""Model-layer invariant tests for the completion-oracle review models.

Covers the validators that keep a filed verdict internally consistent and the
reviewer distinct from the executor at every layer that carries the ids: the
report, the gate result, the archive record, and the trusted runtime context.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.engine.completion_oracle.review_models import (
    MAX_ORACLE_FINDINGS_PER_REPORT,
    CompletionOracleFinding,
    CompletionOracleGateResult,
    CompletionOracleReport,
    CompletionOracleReportRecord,
    CompletionOracleVerdict,
)
from synthorg.engine.completion_oracle.runtime_context import (
    CompletionOracleRuntimeContext,
)
from synthorg.security.redteam.models import RedTeamSeverity

pytestmark = pytest.mark.unit


def _report(
    *,
    reviewer: str = "reviewer-1",
    executor: str = "executor-1",
    verdict: CompletionOracleVerdict = CompletionOracleVerdict.APPROVE,
    ran_tests: bool = False,
    test_command: str | None = None,
) -> CompletionOracleReport:
    return CompletionOracleReport(
        execution_id="exec-1",
        task_id="task-1",
        reviewer_agent_id=reviewer,
        executor_agent_id=executor,
        verdict=verdict,
        summary="reviewed",
        ran_tests=ran_tests,
        test_command=test_command,
    )


class TestReportInvariants:
    def test_reviewer_equal_executor_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must differ"):
            _report(reviewer="same", executor="same")

    def test_test_command_without_ran_tests_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ran_tests is False"):
            _report(ran_tests=False, test_command="pytest -x")

    def test_test_command_with_ran_tests_allowed(self) -> None:
        report = _report(ran_tests=True, test_command="pytest -x")
        assert report.test_command == "pytest -x"

    def test_findings_over_cap_rejected(self) -> None:
        one = CompletionOracleFinding(severity=RedTeamSeverity.LOW, description="minor")
        with pytest.raises(ValidationError, match="maximum"):
            CompletionOracleReport(
                execution_id="exec-1",
                task_id="task-1",
                reviewer_agent_id="reviewer-1",
                executor_agent_id="executor-1",
                verdict=CompletionOracleVerdict.APPROVE,
                findings=(one,) * (MAX_ORACLE_FINDINGS_PER_REPORT + 1),
                summary="reviewed",
            )


class TestFindingInvariants:
    def test_high_severity_requires_evidence(self) -> None:
        with pytest.raises(ValidationError, match="evidence"):
            CompletionOracleFinding(
                severity=RedTeamSeverity.HIGH, description="serious", evidence=()
            )

    def test_low_severity_needs_no_evidence(self) -> None:
        finding = CompletionOracleFinding(
            severity=RedTeamSeverity.LOW, description="minor"
        )
        assert finding.evidence == ()


class TestGateResultInvariants:
    def test_verdict_disagreeing_with_report_rejected(self) -> None:
        report = _report(verdict=CompletionOracleVerdict.REJECT)
        with pytest.raises(ValidationError, match="does not match report"):
            CompletionOracleGateResult(
                verdict=CompletionOracleVerdict.APPROVE,
                report=report,
                elapsed_seconds=0.1,
            )

    def test_matching_verdict_allowed(self) -> None:
        report = _report(verdict=CompletionOracleVerdict.REJECT)
        result = CompletionOracleGateResult(
            verdict=CompletionOracleVerdict.REJECT, report=report, elapsed_seconds=0.1
        )
        assert result.verdict is CompletionOracleVerdict.REJECT


class TestRecordInvariants:
    def test_record_keys_must_match_report(self) -> None:
        report = _report(verdict=CompletionOracleVerdict.REJECT)
        with pytest.raises(ValidationError, match="does not match report"):
            CompletionOracleReportRecord(
                execution_id="exec-1",
                task_id="task-1",
                verdict=CompletionOracleVerdict.APPROVE,
                report=report,
                recorded_at=datetime.now(UTC),
            )


class TestRuntimeContextInvariant:
    def test_reviewer_equal_executor_rejected_before_dispatch(self) -> None:
        # The trust-boundary type fails fast so a self-review is caught at gate
        # ctx construction, not after a full reviewer turn has already run.
        with pytest.raises(ValidationError, match="must differ"):
            CompletionOracleRuntimeContext(
                execution_id="exec-1",
                task_id="task-1",
                reviewer_agent_id="same",
                executor_agent_id="same",
            )

    def test_distinct_identities_allowed(self) -> None:
        ctx = CompletionOracleRuntimeContext(
            execution_id="exec-1",
            task_id="task-1",
            reviewer_agent_id="reviewer-1",
            executor_agent_id="executor-1",
        )
        assert ctx.reviewer_agent_id == "reviewer-1"
