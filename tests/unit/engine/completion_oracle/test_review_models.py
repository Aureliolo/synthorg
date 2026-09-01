"""Model-layer invariant tests for the completion-oracle review models.

Covers the validators that keep a filed verdict internally consistent and the
reviewer distinct from the executor at every layer that carries the ids: the
report, the gate result, the archive record, and the trusted runtime context.
"""

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
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_CLOCK = FakeClock()


_A_FINDING = CompletionOracleFinding(
    severity=RedTeamSeverity.MEDIUM, description="the suite does not run"
)


def _report(
    *,
    reviewer: str = "reviewer-1",
    executor: str = "executor-1",
    verdict: CompletionOracleVerdict = CompletionOracleVerdict.APPROVE,
    test_evidence_cited: bool = False,
    test_command: str | None = None,
) -> CompletionOracleReport:
    rejecting = verdict is CompletionOracleVerdict.REJECT
    return CompletionOracleReport(
        execution_id="exec-1",
        task_id="task-1",
        reviewer_agent_id=reviewer,
        executor_agent_id=executor,
        verdict=verdict,
        findings=(_A_FINDING,) if rejecting else (),
        summary="reviewed",
        test_evidence_cited=test_evidence_cited,
        test_command=test_command,
    )


class TestReportInvariants:
    def test_reviewer_equal_executor_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must differ"):
            _report(reviewer="same", executor="same")

    def test_test_command_without_cited_evidence_rejected(self) -> None:
        with pytest.raises(ValidationError, match="test_evidence_cited is False"):
            _report(test_evidence_cited=False, test_command="pytest -x")

    def test_test_command_with_cited_evidence_allowed(self) -> None:
        report = _report(test_evidence_cited=True, test_command="pytest -x")
        assert report.test_command == "pytest -x"

    def test_rejection_with_no_findings_rejected(self) -> None:
        """A verdict that sends work back must name what is wrong with it.

        The rework brief and every later surface read the structured
        findings; the summary is prose the reviewer chose to write. A
        rejection carrying neither leaves the assignee nothing to act on.
        """
        with pytest.raises(ValidationError, match="at least one finding"):
            CompletionOracleReport(
                execution_id="exec-1",
                task_id="task-1",
                reviewer_agent_id="reviewer-1",
                executor_agent_id="executor-1",
                verdict=CompletionOracleVerdict.REJECT,
                findings=(),
                summary="does not meet the criteria",
            )

    @pytest.mark.parametrize(
        "verdict",
        [
            CompletionOracleVerdict.APPROVE,
            CompletionOracleVerdict.APPROVE_WITH_NOTES,
            CompletionOracleVerdict.ESCALATE,
        ],
    )
    def test_every_other_verdict_may_carry_no_findings(
        self, verdict: CompletionOracleVerdict
    ) -> None:
        """Only a rejection routes rework, so only a rejection owes findings.

        An escalation in particular must stay constructible empty: the gate
        synthesises one for the fail-closed paths, including the case where
        no reviewer ran at all and there is nothing to have found.
        """
        report = CompletionOracleReport(
            execution_id="exec-1",
            task_id="task-1",
            reviewer_agent_id="reviewer-1",
            executor_agent_id="executor-1",
            verdict=verdict,
            findings=(),
            summary="reviewed",
        )
        assert report.findings == ()

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
    @pytest.mark.parametrize(
        ("execution_id", "task_id", "verdict"),
        [
            # Verdict differs from the embedded report.
            ("exec-1", "task-1", CompletionOracleVerdict.APPROVE),
            # execution_id differs while the verdict matches the report.
            ("exec-2", "task-1", CompletionOracleVerdict.REJECT),
            # task_id differs while the verdict matches the report.
            ("exec-1", "task-2", CompletionOracleVerdict.REJECT),
        ],
    )
    def test_record_keys_must_match_report(
        self,
        execution_id: str,
        task_id: str,
        verdict: CompletionOracleVerdict,
    ) -> None:
        report = _report(verdict=CompletionOracleVerdict.REJECT)
        with pytest.raises(ValidationError, match="does not match report"):
            CompletionOracleReportRecord(
                execution_id=execution_id,
                task_id=task_id,
                verdict=verdict,
                report=report,
                recorded_at=_CLOCK.now(),
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
