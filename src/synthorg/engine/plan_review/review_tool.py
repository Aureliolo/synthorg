# module-kind: code
"""Terminal review tool a panellist submits its verdict + findings through.

Mirrors the decomposition submit tool: the review session runs read-only turns,
then calls ``submit_plan_review`` exactly once with a verdict and any findings.
A malformed submission surfaces as a tool error so the reviewer can correct and
resubmit within the same session. The reviewer's role and id are supplied by
the session (not the model), so a panellist cannot spoof who it reviews as.
"""

from typing import Final, cast, override

from pydantic import JsonValue

from synthorg.core.plan_enums import PlanReviewFindingCategory, PlanReviewVerdict
from synthorg.core.plan_review import PlanReviewerVerdict, PlanReviewFinding
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    PlanReviewCategoryGuidanceError,
    PlanReviewParseError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.plan_review import (
    PLAN_REVIEW_REVIEWER_ACCEPTED,
    PLAN_REVIEW_REVIEWER_DUPLICATE_SUBMIT,
    PLAN_REVIEW_VALIDATION_ERROR,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

REVIEW_TOOL_NAME: Final[str] = "submit_plan_review"

_VERDICT_MAP: Final[dict[str, PlanReviewVerdict]] = {
    v.value: v for v in PlanReviewVerdict
}
_CATEGORY_MAP: Final[dict[str, PlanReviewFindingCategory]] = {
    c.value: c for c in PlanReviewFindingCategory
}

#: What each finding kind means, in the reviewer's own terms. One owner for
#: the vocabulary: the tool schema and the review brief both render from this,
#: so a category the brief asks about can never be one the schema omits. A new
#: enum member with no entry here fails ``render_category_guidance``.
CATEGORY_GUIDANCE: Final[dict[PlanReviewFindingCategory, str]] = {
    PlanReviewFindingCategory.GAP: "something the plan is missing entirely",
    PlanReviewFindingCategory.MISSING_OWNER: ("an item no accountable role owns"),
    PlanReviewFindingCategory.MISCALIBRATED_STAKES: (
        "a stakes level that does not match the work: everything critical, or "
        "something irreversible left as normal"
    ),
    PlanReviewFindingCategory.RISKY_DECISION: (
        "a decision item whose options or recommendation do not hold up"
    ),
    PlanReviewFindingCategory.BUDGET_CONCERN: "cost",
    PlanReviewFindingCategory.SEQUENCING: (
        "a claim about the graph rather than the items: work ordered wrongly, "
        "serialised when it could run in parallel, or an item that cannot "
        "possibly precede the work it depends on"
    ),
    PlanReviewFindingCategory.UNVERIFIABLE_CRITERIA: (
        "an item whose definition of done cannot be checked"
    ),
    PlanReviewFindingCategory.OVERSIZED_SCOPE: (
        "one item carrying what should be several"
    ),
    PlanReviewFindingCategory.OTHER: (
        "none of the above; use this last, and say what kind of problem it is"
    ),
}


def render_category_guidance() -> str:
    """Render the finding vocabulary as reviewer-facing lines.

    Returns:
        One ``- name: meaning`` line per category, in enum order.

    Raises:
        PlanReviewCategoryGuidanceError: If a category carries no meaning. A
            reviewer shown a name it was never told the sense of proposes its
            own, which is the behaviour the vocabulary exists to remove.
    """
    missing = sorted(
        category.value
        for category in PlanReviewFindingCategory
        if category not in CATEGORY_GUIDANCE
    )
    if missing:
        msg = f"finding categories carry no reviewer guidance: {missing}"
        raise PlanReviewCategoryGuidanceError(msg)
    return "\n".join(
        f"- {category.value}: {CATEGORY_GUIDANCE[category]}"
        for category in PlanReviewFindingCategory
    )


def build_review_tool_schema() -> dict[str, JsonValue]:
    """Build the JSON Schema for the ``submit_plan_review`` tool.

    Returns:
        The parameters schema describing a verdict plus zero or more findings.
    """
    return {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": [v.value for v in PlanReviewVerdict],
                "description": (
                    "Your overall verdict. 'endorsed' backs the plan as-is; "
                    "'concerns' backs it but raises findings to address; "
                    "'revision_requested' sends it back to the owner to revise."
                ),
            },
            "findings": {
                "type": "array",
                "description": (
                    "Concrete concerns you raise. Empty only when you endorse "
                    "the plan with no reservations."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": [c.value for c in PlanReviewFindingCategory],
                            "description": (
                                "The kind of gap this finding flags:\n"
                                f"{render_category_guidance()}"
                            ),
                        },
                        "detail": {
                            "type": "string",
                            "description": (
                                "A concrete, actionable description of the concern"
                            ),
                        },
                        "item_id": {
                            "type": ["string", "null"],
                            "description": (
                                "The plan item id this concerns, or null when it is "
                                "a plan-level concern (e.g. a missing workstream)."
                            ),
                        },
                    },
                    "required": ["category", "detail"],
                },
            },
        },
        "required": ["verdict"],
    }


def parse_reviewer_verdict(
    arguments: dict[str, JsonValue],
    *,
    reviewer_role: NotBlankStr,
    reviewer_id: NotBlankStr,
) -> PlanReviewerVerdict:
    """Parse a submitted review into a :class:`PlanReviewerVerdict`.

    Args:
        arguments: The tool-call arguments (a verdict and optional findings).
        reviewer_role: The role the panellist reviews as (session-supplied).
        reviewer_id: The reviewing agent's id (session-supplied).

    Returns:
        The parsed, validated per-reviewer verdict.

    Raises:
        PlanReviewParseError: If the verdict is missing/unknown or a finding
            is malformed.
    """
    raw_verdict = arguments.get("verdict")
    if not isinstance(raw_verdict, str) or raw_verdict.lower() not in _VERDICT_MAP:
        msg = f"missing or unknown verdict {raw_verdict!r}"
        raise PlanReviewParseError(msg)
    verdict = _VERDICT_MAP[raw_verdict.lower()]
    findings = _parse_findings(arguments.get("findings"))
    if verdict is not PlanReviewVerdict.ENDORSED and not findings:
        msg = "findings are required unless the verdict is endorsed"
        raise PlanReviewParseError(msg)
    try:
        return PlanReviewerVerdict(
            reviewer_role=reviewer_role,
            reviewer_id=reviewer_id,
            verdict=verdict,
            findings=findings,
        )
    except ValueError as exc:
        raise PlanReviewParseError(str(exc)) from exc


def _parse_findings(raw: JsonValue) -> tuple[PlanReviewFinding, ...]:
    """Parse the findings array into validated findings.

    Returns:
        The parsed findings (empty when ``raw`` is absent).

    Raises:
        PlanReviewParseError: If a finding is malformed or its category unknown.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        msg = f"findings must be a list, got {type(raw).__name__}"
        raise PlanReviewParseError(msg)
    parsed: list[PlanReviewFinding] = []
    for entry in raw:
        if not isinstance(entry, dict):
            msg = f"each finding must be an object, got {type(entry).__name__}"
            raise PlanReviewParseError(msg)
        parsed.append(_parse_finding(entry))
    return tuple(parsed)


def _parse_finding(entry: dict[str, JsonValue]) -> PlanReviewFinding:
    """Parse one finding object into a :class:`PlanReviewFinding`.

    Returns:
        The validated finding.

    Raises:
        PlanReviewParseError: If the category is unknown or the detail is blank.
    """
    raw_category = entry.get("category")
    if not isinstance(raw_category, str) or raw_category.lower() not in _CATEGORY_MAP:
        msg = f"missing or unknown finding category {raw_category!r}"
        raise PlanReviewParseError(msg)
    raw_detail = entry.get("detail")
    if not isinstance(raw_detail, str) or not raw_detail.strip():
        msg = "finding detail must be a non-empty string"
        raise PlanReviewParseError(msg)
    raw_item = entry.get("item_id")
    if raw_item is None:
        item_id = None
    elif isinstance(raw_item, str) and raw_item.strip():
        item_id = NotBlankStr(raw_item)
    else:
        msg = f"finding item_id must be a non-blank string or omitted, got {raw_item!r}"
        raise PlanReviewParseError(msg)
    return PlanReviewFinding(
        category=_CATEGORY_MAP[raw_category.lower()],
        detail=NotBlankStr(raw_detail),
        item_id=item_id,
    )


class VerdictCapture:
    """Mutable holder for the verdict a session submits via the review tool."""

    __slots__ = ("verdict",)

    def __init__(self) -> None:
        self.verdict: PlanReviewerVerdict | None = None


class SubmitPlanReviewTool(BaseTool):
    """Terminal review tool: the panellist submits its verdict through it.

    The parsed verdict is captured for the session to consolidate. A malformed
    submission surfaces as a tool error so the reviewer corrects and resubmits.
    """

    def __init__(
        self,
        *,
        reviewer_role: NotBlankStr,
        reviewer_id: NotBlankStr,
        capture: VerdictCapture,
    ) -> None:
        super().__init__(
            name=REVIEW_TOOL_NAME,
            description=(
                "Submit your review of the plan. Give a verdict and any concrete "
                "findings (each with a category, a detail, and the plan item id it "
                "concerns when item-specific). Call this exactly once, last, after "
                "you have read the whole plan."
            ),
            parameters_schema=build_review_tool_schema(),
            category=ToolCategory.OTHER,
        )
        self._reviewer_role = reviewer_role
        self._reviewer_id = reviewer_id
        self._capture = capture

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Parse + capture the submitted verdict, or report a correctable error.

        Returns:
            A success result acknowledging the verdict, or an error result
            describing why it was rejected so the reviewer resubmits.
        """
        try:
            verdict = parse_reviewer_verdict(
                cast("dict[str, JsonValue]", arguments),
                reviewer_role=self._reviewer_role,
                reviewer_id=self._reviewer_id,
            )
        except PlanReviewParseError as exc:
            logger.warning(
                PLAN_REVIEW_VALIDATION_ERROR,
                reviewer_id=self._reviewer_id,
                reviewer_role=self._reviewer_role,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    f"Review rejected: {exc}. Fix the issue and call "
                    f"{REVIEW_TOOL_NAME} again."
                ),
                is_error=True,
            )
        if self._capture.verdict is not None:
            # The tool is instructed to be called once; a second call must not
            # replace the verdict the session later consolidates, so reject it
            # and keep the first submission.
            logger.warning(
                PLAN_REVIEW_REVIEWER_DUPLICATE_SUBMIT,
                reviewer_id=self._reviewer_id,
                reviewer_role=self._reviewer_role,
            )
            return ToolExecutionResult(
                content=(
                    "Review already submitted; the first verdict stands. "
                    "Stop now rather than resubmitting."
                ),
                is_error=True,
            )
        self._capture.verdict = verdict
        logger.debug(
            PLAN_REVIEW_REVIEWER_ACCEPTED,
            reviewer_id=self._reviewer_id,
            reviewer_role=self._reviewer_role,
            verdict=verdict.verdict.value,
            finding_count=len(verdict.findings),
        )
        return ToolExecutionResult(
            content=(
                f"Review accepted ({verdict.verdict.value}, "
                f"{len(verdict.findings)} finding(s)). You may stop now."
            ),
        )
