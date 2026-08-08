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
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

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


class PlanReviewOutcome(BaseModel):
    """What a review attempt produced, and why when it produced nothing.

    A bare ``None`` said two different things at once: "no panel was
    seated" and "a seated panel returned nothing". Neither reached the
    operator, who saw a plan with an empty review section and no way to
    tell whether it had been scrutinised and passed or never looked at.

    Attributes:
        review: The consolidated review, when the panel produced one.
        absent_reason: Why no review was produced. Always set when
            ``review`` is ``None``, so an unreviewed plan carries the
            reason to the approval gate rather than a blank section.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    review: PlanReview | None = Field(
        default=None,
        description="The consolidated stakeholder-panel review, if one was made",
    )
    absent_reason: NotBlankStr | None = Field(
        default=None,
        description="Why no review was produced",
    )

    @model_validator(mode="after")
    def _validate_exactly_one(self) -> Self:
        """Reject an outcome that says nothing, or says two things.

        Returns:
            ``self`` when exactly one of ``review`` / ``absent_reason`` is set.

        Raises:
            ValueError: When both or neither is set. Both would leave the
                surface choosing which to believe; neither is the silent
                blank this type exists to make impossible.
        """
        if (self.review is None) == (self.absent_reason is None):
            msg = "a review outcome carries either a review or a reason, not both"
            raise ValueError(msg)
        return self
