# module-kind: code
"""Value models for a company plan review.

A greenlit plan is reviewed by a bounded panel of stakeholders (a lead per
relevant lens: technical, budget, domain) before it reaches the human. Each
panellist contributes a :class:`PlanReviewerVerdict` (their verdict plus the
findings they raised); the panel is consolidated into one :class:`PlanReview`
attached to the plan, so the human sees who reviewed it and what they flagged.

Findings reference plan items by id (a plain string) rather than importing
``PlanItem``, so this module stays a leaf that ``core.plan`` can import without
a cycle.
"""

from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from synthorg.core.plan_enums import PlanReviewFindingCategory, PlanReviewVerdict
from synthorg.core.types import NotBlankStr


class PlanReviewFinding(BaseModel):
    """One concern a reviewer raised about a plan.

    Attributes:
        category: The kind of gap this finding flags.
        detail: A concrete, human-readable description of the concern.
        item_id: The plan item the finding concerns, or ``None`` when it is a
            plan-level concern (e.g. a missing workstream).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    category: PlanReviewFindingCategory = Field(description="The kind of gap flagged")
    detail: NotBlankStr = Field(description="Human-readable description of the concern")
    item_id: NotBlankStr | None = Field(
        default=None,
        description="The plan item this finding concerns, or None if plan-level",
    )


class PlanReviewerVerdict(BaseModel):
    """One panellist's contribution to a plan review.

    Attributes:
        reviewer_role: The role the reviewer sits on the panel as (e.g. 'CTO').
        reviewer_id: The reviewing agent's identifier.
        verdict: This reviewer's verdict (endorsed, or concerns raised).
        findings: The concerns this reviewer raised (typically empty when
            endorsed, though an endorsement may still note minor findings).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    reviewer_role: NotBlankStr = Field(description="Role the reviewer reviews as")
    reviewer_id: NotBlankStr = Field(description="The reviewing agent's identifier")
    verdict: PlanReviewVerdict = Field(description="This reviewer's verdict")
    findings: tuple[PlanReviewFinding, ...] = Field(
        default=(),
        description="Concerns this reviewer raised",
    )


class PlanReview(BaseModel):
    """The consolidated panel review attached to a plan.

    Attributes:
        verdict: The synthesised overall verdict across the panel.
        reviewers: Each panellist's verdict and findings.
        summary: A short synthesis of the panel's position, if any.
        reviewed_at: When the panel review was consolidated (tz-aware UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    verdict: PlanReviewVerdict = Field(description="Synthesised overall verdict")
    reviewers: tuple[PlanReviewerVerdict, ...] = Field(
        min_length=1,
        description="Each panellist's verdict and findings",
    )
    summary: NotBlankStr | None = Field(
        default=None,
        description="Short synthesis of the panel's position",
    )
    reviewed_at: AwareDatetime = Field(
        description="When the panel review was consolidated (tz-aware UTC)",
    )

    @field_validator("reviewed_at")
    @classmethod
    def _normalize_reviewed_at(cls, value: datetime) -> datetime:
        """Convert the aware timestamp to UTC so the field honours its contract.

        ``AwareDatetime`` already rejects naive input; this collapses any
        non-UTC offset to UTC so a persisted review time cannot contradict the
        model's stated UTC invariant.

        Returns:
            The same instant expressed in UTC.
        """
        return value.astimezone(UTC)
