"""Unit tests for plan-review synthesis."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.plan_enums import PlanReviewFindingCategory, PlanReviewVerdict
from synthorg.core.plan_review import PlanReviewerVerdict, PlanReviewFinding
from synthorg.core.types import NotBlankStr
from synthorg.engine.plan_review.synthesis import synthesise_review

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


def _reviewer(
    role: str,
    verdict: PlanReviewVerdict,
    *findings: PlanReviewFinding,
) -> PlanReviewerVerdict:
    return PlanReviewerVerdict(
        reviewer_role=NotBlankStr(role),
        reviewer_id=NotBlankStr(f"agent-{role}"),
        verdict=verdict,
        findings=findings,
    )


def _finding(detail: str) -> PlanReviewFinding:
    return PlanReviewFinding(
        category=PlanReviewFindingCategory.GAP, detail=NotBlankStr(detail)
    )


class TestSynthesiseReview:
    def test_all_endorsed_yields_endorsed(self) -> None:
        review = synthesise_review(
            (
                _reviewer("CTO", PlanReviewVerdict.ENDORSED),
                _reviewer("CFO", PlanReviewVerdict.ENDORSED),
            ),
            now=_NOW,
        )
        assert review.verdict is PlanReviewVerdict.ENDORSED
        assert review.summary is not None
        assert "endorsed" in review.summary.lower()

    def test_most_severe_verdict_wins(self) -> None:
        review = synthesise_review(
            (
                _reviewer("CTO", PlanReviewVerdict.ENDORSED),
                _reviewer("CFO", PlanReviewVerdict.CONCERNS, _finding("over budget")),
                _reviewer("QA Lead", PlanReviewVerdict.REVISION_REQUESTED),
            ),
            now=_NOW,
        )
        assert review.verdict is PlanReviewVerdict.REVISION_REQUESTED

    def test_concerns_outrank_endorsed(self) -> None:
        review = synthesise_review(
            (
                _reviewer("CTO", PlanReviewVerdict.ENDORSED),
                _reviewer("CFO", PlanReviewVerdict.CONCERNS, _finding("risk")),
            ),
            now=_NOW,
        )
        assert review.verdict is PlanReviewVerdict.CONCERNS
        assert review.summary is not None
        assert "1 of 2" in review.summary

    def test_reviewed_at_is_the_supplied_time(self) -> None:
        review = synthesise_review(
            (_reviewer("CTO", PlanReviewVerdict.ENDORSED),), now=_NOW
        )
        assert review.reviewed_at == _NOW

    def test_naive_datetime_is_rejected(self) -> None:
        naive = datetime(2026, 4, 1, 12, 0)  # noqa: DTZ001 -- exercising the guard
        with pytest.raises(ValidationError):
            synthesise_review(
                (_reviewer("CTO", PlanReviewVerdict.ENDORSED),), now=naive
            )

    def test_empty_reviewers_raises(self) -> None:
        with pytest.raises(ValueError, match="no reviewers"):
            synthesise_review((), now=_NOW)
