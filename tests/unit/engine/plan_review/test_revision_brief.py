"""Tests for the brief that sends a reviewed plan back to be re-planned.

The panel's whole purpose is to catch a plan before the operator has to. Until
this brief existed its findings were persisted, rendered and then ignored: a
live run produced 18 findings across 4 reviewers and the plan went to the
operator with all 18 outstanding, because nothing turned a verdict into another
planning pass.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.plan_enums import PlanReviewFindingCategory, PlanReviewVerdict
from synthorg.core.plan_review import (
    PlanReview,
    PlanReviewerVerdict,
    PlanReviewFinding,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.plan_review.revision_brief import (
    build_revision_brief,
    review_demands_revision,
)

pytestmark = pytest.mark.unit

_AT = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)


def _review(
    verdict: PlanReviewVerdict,
    *findings: PlanReviewFinding,
) -> PlanReview:
    """Build a one-reviewer review carrying *findings*."""
    return PlanReview(
        verdict=verdict,
        reviewers=(
            PlanReviewerVerdict(
                reviewer_role=NotBlankStr("CTO"),
                reviewer_id=NotBlankStr("agent-1"),
                verdict=verdict,
                findings=findings,
            ),
        ),
        reviewed_at=_AT,
    )


def _finding(detail: str) -> PlanReviewFinding:
    """Build a plan-level finding carrying *detail*."""
    return PlanReviewFinding(
        category=PlanReviewFindingCategory.GAP,
        detail=NotBlankStr(detail),
    )


class TestReviewDemandsRevision:
    def test_an_endorsement_with_no_findings_is_settled(self) -> None:
        assert not review_demands_revision(_review(PlanReviewVerdict.ENDORSED))

    def test_concerns_demand_revision(self) -> None:
        """18 concerns reaching the operator unaddressed is the defect."""
        review = _review(
            PlanReviewVerdict.CONCERNS, _finding("no item builds the game")
        )
        assert review_demands_revision(review)

    def test_revision_requested_demands_revision(self) -> None:
        """The tool promises this verdict sends the plan back. It must."""
        review = _review(PlanReviewVerdict.REVISION_REQUESTED, _finding("circular dep"))
        assert review_demands_revision(review)

    def test_an_endorsement_that_still_raised_a_finding_demands_revision(self) -> None:
        """An endorsement may note findings; a noted gap is still a gap."""
        review = _review(PlanReviewVerdict.ENDORSED, _finding("no owner for storage"))
        assert review_demands_revision(review)

    def test_no_review_demands_nothing(self) -> None:
        """No panel attached is not a demand for revision.

        Fail-open here is deliberate: an unwired panel must not loop the
        planner for ever against an opinion nobody offered.
        """
        assert not review_demands_revision(None)


class TestBuildRevisionBrief:
    def test_the_brief_carries_every_finding(self) -> None:
        review = _review(
            PlanReviewVerdict.CONCERNS,
            _finding("no item builds the engine"),
            _finding("item 3 depends on a test item 4 authors"),
        )
        brief = build_revision_brief(review=review, note=None)
        assert "no item builds the engine" in brief
        assert "item 3 depends on a test item 4 authors" in brief

    def test_the_brief_names_the_reviewer_so_the_planner_can_weigh_it(self) -> None:
        review = _review(PlanReviewVerdict.CONCERNS, _finding("budget unvalidated"))
        assert "CTO" in build_revision_brief(review=review, note=None)

    def test_an_operator_note_is_carried_and_fenced(self) -> None:
        """The operator's own words are untrusted input at the LLM boundary."""
        brief = build_revision_brief(review=None, note="the workspace is empty")
        assert "the workspace is empty" in brief
        assert "<task-data>" in brief

    def test_the_operator_note_leads(self) -> None:
        """A human asking for a change outranks a panel opinion."""
        review = _review(PlanReviewVerdict.CONCERNS, _finding("panel point"))
        brief = build_revision_brief(review=review, note="operator point")
        assert brief.index("operator point") < brief.index("panel point")

    def test_a_brief_with_neither_source_is_refused(self) -> None:
        """Re-planning against nothing would burn a round for no reason."""
        with pytest.raises(ValueError, match="nothing to revise"):
            build_revision_brief(review=None, note=None)
