# module-kind: code
"""Consolidate a panel's per-reviewer verdicts into one plan review.

The synthesis is deterministic (no extra LLM chokepoint): the overall verdict
is the most severe verdict any panellist reached, and the summary is a plain
count of who reviewed and how many concerns they raised. Keeping it mechanical
makes the consolidation cheap, testable, and free of a second cost-attributed
call, while the reviewers' own findings carry the substance.
"""

from datetime import datetime

from synthorg.core.plan_enums import PlanReviewVerdict
from synthorg.core.plan_review import PlanReview, PlanReviewerVerdict
from synthorg.core.types import NotBlankStr

#: Severity order of verdicts, most severe last. The synthesised overall
#: verdict is the most severe any single reviewer reached: one revision request
#: sends the whole plan back, and any raised concern outranks a clean endorse.
_VERDICT_SEVERITY: dict[PlanReviewVerdict, int] = {
    PlanReviewVerdict.ENDORSED: 0,
    PlanReviewVerdict.CONCERNS: 1,
    PlanReviewVerdict.REVISION_REQUESTED: 2,
}


def synthesise_review(
    reviewers: tuple[PlanReviewerVerdict, ...],
    *,
    now: datetime,
) -> PlanReview:
    """Consolidate *reviewers* into a single :class:`PlanReview`.

    Args:
        reviewers: Each panellist's verdict and the findings they raised.
        now: The consolidation timestamp (normalised to tz-aware UTC).

    Returns:
        A :class:`PlanReview` whose overall verdict is the most severe any
        reviewer reached and whose summary counts reviewers and concerns.

    Raises:
        ValueError: If ``reviewers`` is empty (a review with no panellist has
            nothing to consolidate).
    """
    if not reviewers:
        msg = "cannot synthesise a plan review with no reviewers"
        raise ValueError(msg)
    overall = max(reviewers, key=lambda r: _VERDICT_SEVERITY[r.verdict]).verdict
    finding_count = sum(len(r.findings) for r in reviewers)
    concern_count = sum(
        1 for r in reviewers if r.verdict is not PlanReviewVerdict.ENDORSED
    )
    summary = _summary(len(reviewers), concern_count, finding_count)
    return PlanReview(
        verdict=overall,
        reviewers=reviewers,
        summary=NotBlankStr(summary),
        reviewed_at=now,
    )


def _summary(reviewer_count: int, concern_count: int, finding_count: int) -> str:
    """Compose the panel's one-line synthesis.

    Returns:
        A plain-English summary of panel size, how many raised concerns, and
        the total findings, so the human sees the panel's shape at a glance.
    """
    if concern_count == 0:
        return f"All {reviewer_count} reviewer(s) endorsed the plan as-is."
    return (
        f"{concern_count} of {reviewer_count} reviewer(s) raised "
        f"{finding_count} finding(s) for the owner to address."
    )
