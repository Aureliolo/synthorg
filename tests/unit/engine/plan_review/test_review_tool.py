"""Unit tests for the plan-review submit tool and its parser."""

import pytest

from synthorg.core.plan_enums import PlanReviewFindingCategory, PlanReviewVerdict
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    PlanReviewCategoryGuidanceError,
    PlanReviewParseError,
)
from synthorg.engine.plan_review.review_tool import (
    CATEGORY_GUIDANCE,
    SubmitPlanReviewTool,
    VerdictCapture,
    build_review_tool_schema,
    parse_reviewer_verdict,
    render_category_guidance,
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

    @pytest.mark.parametrize("category", list(PlanReviewFindingCategory))
    def test_every_category_the_schema_offers_parses(
        self, category: PlanReviewFindingCategory
    ) -> None:
        # A category the schema advertises and the parser rejects costs the
        # reviewer a turn and lands the finding on a worse kind, which is the
        # behaviour that made the vocabulary too narrow to begin with.
        verdict = parse_reviewer_verdict(
            {
                "verdict": "concerns",
                "findings": [{"category": category.value, "detail": "a concern"}],
            },
            reviewer_role=_ROLE,
            reviewer_id=_ID,
        )
        assert verdict.findings[0].category is category

    def test_schema_offers_exactly_the_enum(self) -> None:
        schema = build_review_tool_schema()
        properties = schema["properties"]
        assert isinstance(properties, dict)
        findings = properties["findings"]
        assert isinstance(findings, dict)
        items = findings["items"]
        assert isinstance(items, dict)
        item_properties = items["properties"]
        assert isinstance(item_properties, dict)
        category = item_properties["category"]
        assert isinstance(category, dict)
        assert category["enum"] == [c.value for c in PlanReviewFindingCategory]

    def test_every_category_carries_reviewer_guidance(self) -> None:
        # The brief and the schema render from one mapping, so a category
        # present in the enum and absent from the mapping would reach a
        # reviewer as a bare name and it would propose its own instead.
        rendered = render_category_guidance()
        for category in PlanReviewFindingCategory:
            assert f"- {category.value}: " in rendered

    def test_a_category_with_no_guidance_fails_the_render(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delitem(CATEGORY_GUIDANCE, PlanReviewFindingCategory.SEQUENCING)
        with pytest.raises(PlanReviewCategoryGuidanceError, match="sequencing"):
            render_category_guidance()

    def test_a_sequencing_claim_has_its_own_category(self) -> None:
        # Run 1's C10: six items, zero dependency edges, and an item naming
        # three it declares no dependency on. The closest members were GAP
        # (which reads as a missing item) and OTHER (which discards the kind).
        verdict = parse_reviewer_verdict(
            {
                "verdict": "revision_requested",
                "findings": [
                    {
                        "category": "sequencing",
                        "detail": (
                            "'Integrate game loop' names the engine, renderer "
                            "and input items but declares no dependency on any "
                            "of them, and the graph has zero edges"
                        ),
                        "item_id": "item-6",
                    }
                ],
            },
            reviewer_role=_ROLE,
            reviewer_id=_ID,
        )
        assert verdict.findings[0].category is PlanReviewFindingCategory.SEQUENCING

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
