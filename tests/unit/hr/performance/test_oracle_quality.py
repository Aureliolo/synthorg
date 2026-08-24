"""Tests for the completion-oracle verdict to quality-score mapping."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleFinding,
    CompletionOracleReport,
    CompletionOracleVerdict,
)
from synthorg.hr.performance.oracle_quality import (
    APPROVE_QUALITY_SCORE,
    APPROVE_WITH_NOTES_QUALITY_SCORE,
    HIGH_SEVERITY_FINDING_DISCOUNT,
    MIN_QUALITY_SCORE,
    REJECT_QUALITY_SCORE,
    quality_score_for,
)
from synthorg.security.redteam.models import RedTeamSeverity

pytestmark = pytest.mark.unit


def _finding(severity: RedTeamSeverity) -> CompletionOracleFinding:
    """Build a finding at *severity*, with evidence when the model demands it.

    Returns:
        A valid ``CompletionOracleFinding``.
    """
    needs_evidence = severity in (RedTeamSeverity.HIGH, RedTeamSeverity.CRITICAL)
    return CompletionOracleFinding(
        severity=severity,
        description=NotBlankStr("an observation"),
        evidence=(NotBlankStr("quoted line"),) if needs_evidence else (),
    )


def _report(
    verdict: CompletionOracleVerdict,
    *,
    findings: tuple[CompletionOracleFinding, ...] = (),
) -> CompletionOracleReport:
    """Build a report carrying *verdict* and *findings*.

    Returns:
        A valid ``CompletionOracleReport``.
    """
    return CompletionOracleReport(
        execution_id=NotBlankStr("exec-1"),
        task_id=NotBlankStr("task-1"),
        reviewer_agent_id=NotBlankStr("reviewer-1"),
        executor_agent_id=NotBlankStr("executor-1"),
        verdict=verdict,
        findings=findings,
        summary=NotBlankStr("reviewed"),
    )


class TestVerdictBands:
    """Each verdict maps to its own band."""

    def test_approve_scores_the_ceiling(self) -> None:
        assert quality_score_for(_report(CompletionOracleVerdict.APPROVE)) == (
            APPROVE_QUALITY_SCORE
        )

    def test_approve_with_notes_scores_below_a_clean_approval(self) -> None:
        score = quality_score_for(
            _report(
                CompletionOracleVerdict.APPROVE_WITH_NOTES,
                findings=(_finding(RedTeamSeverity.LOW),),
            )
        )
        assert score == APPROVE_WITH_NOTES_QUALITY_SCORE
        assert APPROVE_WITH_NOTES_QUALITY_SCORE < APPROVE_QUALITY_SCORE

    def test_reject_scores_below_every_approval(self) -> None:
        score = quality_score_for(
            _report(
                CompletionOracleVerdict.REJECT,
                findings=(_finding(RedTeamSeverity.LOW),),
            )
        )
        assert score == REJECT_QUALITY_SCORE
        assert REJECT_QUALITY_SCORE < APPROVE_WITH_NOTES_QUALITY_SCORE

    def test_escalate_is_unmeasured_not_a_low_score(self) -> None:
        # No confident verdict was reached, so there is no quality judgement to
        # record. A number here would be a fabricated one.
        assert quality_score_for(_report(CompletionOracleVerdict.ESCALATE)) is None


class TestSeverityDiscount:
    """Blocking-severity findings discount within the verdict's band."""

    @pytest.mark.parametrize(
        "severity",
        [RedTeamSeverity.INFO, RedTeamSeverity.LOW, RedTeamSeverity.MEDIUM],
    )
    def test_sub_blocking_findings_do_not_discount(
        self, severity: RedTeamSeverity
    ) -> None:
        score = quality_score_for(
            _report(
                CompletionOracleVerdict.APPROVE_WITH_NOTES,
                findings=(_finding(severity), _finding(severity)),
            )
        )
        assert score == APPROVE_WITH_NOTES_QUALITY_SCORE

    @pytest.mark.parametrize(
        "severity", [RedTeamSeverity.HIGH, RedTeamSeverity.CRITICAL]
    )
    def test_each_blocking_finding_discounts_once(
        self, severity: RedTeamSeverity
    ) -> None:
        score = quality_score_for(
            _report(
                CompletionOracleVerdict.APPROVE_WITH_NOTES,
                findings=(_finding(severity), _finding(severity)),
            )
        )
        assert score == pytest.approx(
            APPROVE_WITH_NOTES_QUALITY_SCORE - 2 * HIGH_SEVERITY_FINDING_DISCOUNT
        )

    def test_the_discount_floors_rather_than_going_negative(self) -> None:
        findings = tuple(_finding(RedTeamSeverity.CRITICAL) for _ in range(25))
        score = quality_score_for(
            _report(CompletionOracleVerdict.REJECT, findings=findings)
        )
        assert score == MIN_QUALITY_SCORE

    def test_escalate_stays_unmeasured_however_many_findings(self) -> None:
        score = quality_score_for(
            _report(
                CompletionOracleVerdict.ESCALATE,
                findings=(_finding(RedTeamSeverity.CRITICAL),),
            )
        )
        assert score is None


class TestTotality:
    """Every verdict the enum can produce resolves."""

    def test_the_map_covers_every_verdict(self) -> None:
        # A new ``CompletionOracleVerdict`` member fails here (and at
        # type-check time via ``assert_never``) rather than silently
        # resolving to whatever the last branch happened to return.
        for verdict in CompletionOracleVerdict:
            findings = (
                (_finding(RedTeamSeverity.LOW),)
                if verdict is CompletionOracleVerdict.REJECT
                else ()
            )
            score = quality_score_for(_report(verdict, findings=findings))
            assert score is None or MIN_QUALITY_SCORE <= score <= APPROVE_QUALITY_SCORE
