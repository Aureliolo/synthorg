"""Unit tests for the plan-review submit tool and its parser."""

import pytest

from synthorg.core.plan_enums import PlanReviewFindingCategory, PlanReviewVerdict
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import PlanReviewParseError
from synthorg.engine.plan_review.review_tool import (
    SubmitPlanReviewTool,
    VerdictCapture,
    parse_reviewer_verdict,
)

pytestmark = pytest.mark.unit

_ROLE = NotBlankStr("CTO")
_ID = NotBlankStr("agent-cto")


class TestParseReviewerVerdict:
    def test_parses_verdict_and_findings(self) -> None:
        verdict = parse_reviewer_verdict(
            {
                "verdict": "concerns",
                "findings": [
                    {
                        "category": "missing_owner",
                        "detail": "Item 3 has no owner",
                        "item_id": "item-3",
                    },
                ],
            },
            reviewer_role=_ROLE,
            reviewer_id=_ID,
        )
        assert verdict.verdict is PlanReviewVerdict.CONCERNS
        assert verdict.reviewer_role == _ROLE
        assert len(verdict.findings) == 1
        assert verdict.findings[0].category is PlanReviewFindingCategory.MISSING_OWNER
        assert verdict.findings[0].item_id == "item-3"

    def test_endorsed_with_no_findings(self) -> None:
        verdict = parse_reviewer_verdict(
            {"verdict": "endorsed"}, reviewer_role=_ROLE, reviewer_id=_ID
        )
        assert verdict.verdict is PlanReviewVerdict.ENDORSED
        assert verdict.findings == ()

    def test_plan_level_finding_has_no_item_id(self) -> None:
        verdict = parse_reviewer_verdict(
            {
                "verdict": "concerns",
                "findings": [{"category": "gap", "detail": "no rollback plan"}],
            },
            reviewer_role=_ROLE,
            reviewer_id=_ID,
        )
        assert verdict.findings[0].item_id is None

    def test_unknown_verdict_raises(self) -> None:
        with pytest.raises(PlanReviewParseError, match="verdict"):
            parse_reviewer_verdict(
                {"verdict": "looks_good"}, reviewer_role=_ROLE, reviewer_id=_ID
            )

    def test_missing_verdict_raises(self) -> None:
        with pytest.raises(PlanReviewParseError, match="verdict"):
            parse_reviewer_verdict({}, reviewer_role=_ROLE, reviewer_id=_ID)

    def test_unknown_finding_category_raises(self) -> None:
        with pytest.raises(PlanReviewParseError, match="category"):
            parse_reviewer_verdict(
                {
                    "verdict": "concerns",
                    "findings": [{"category": "vibes", "detail": "off"}],
                },
                reviewer_role=_ROLE,
                reviewer_id=_ID,
            )

    def test_blank_finding_detail_raises(self) -> None:
        with pytest.raises(PlanReviewParseError, match="detail"):
            parse_reviewer_verdict(
                {
                    "verdict": "concerns",
                    "findings": [{"category": "gap", "detail": "   "}],
                },
                reviewer_role=_ROLE,
                reviewer_id=_ID,
            )


class TestSubmitPlanReviewTool:
    async def test_captures_a_valid_verdict(self) -> None:
        capture = VerdictCapture()
        tool = SubmitPlanReviewTool(
            reviewer_role=_ROLE, reviewer_id=_ID, capture=capture
        )
        result = await tool.execute(arguments={"verdict": "endorsed"})
        assert not result.is_error
        assert capture.verdict is not None
        assert capture.verdict.verdict is PlanReviewVerdict.ENDORSED

    async def test_malformed_submission_is_a_correctable_tool_error(self) -> None:
        capture = VerdictCapture()
        tool = SubmitPlanReviewTool(
            reviewer_role=_ROLE, reviewer_id=_ID, capture=capture
        )
        result = await tool.execute(arguments={"verdict": "nope"})
        assert result.is_error
        assert capture.verdict is None
        assert "submit_plan_review" in result.content
