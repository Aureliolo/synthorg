# module-kind: code
"""Turn a review verdict and an operator note into another planning pass.

The panel exists to catch a plan before the operator has to, and until this
module the catching went nowhere: a verdict was synthesised, persisted onto
``Plan.review`` and rendered, and the plan was parked for the human regardless.
``PlanReviewVerdict.REVISION_REQUESTED`` was the sharper half of the same gap,
appearing only as an enum member, a severity number and a line in the reviewing
tool's own description promising that it "sends it back to the owner to
revise", with nothing anywhere that sent it.

The operator's ``request-changes`` note had the same shape: recorded on an
audit event, the plan flipped to ``DRAFT``, and the service docstring deferred
"turning it into a concrete replan" to a wiring layer that did not exist.

Both are demands for the same thing, so both produce the same brief and drive
the same pass. The operator's words lead when present: a human asking for a
change outranks a panel opinion.
"""

from synthorg.core.plan_enums import PlanReviewVerdict
from synthorg.core.plan_review import PlanReview
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted

_HEADER = "## Revision requested: this plan was reviewed and must be re-planned"

_CLOSING = (
    "Re-plan the objective addressing every point above. Keep what is sound; "
    "change what is not. Where a point says something does not exist, plan the "
    "work that builds it rather than the work that integrates it."
)


def review_demands_revision(review: PlanReview | None) -> bool:
    """Whether *review* asks for another planning pass.

    Any finding counts, whatever the verdict carrying it: an endorsement that
    still notes a gap has still noted a gap, and the live run that prompted
    this produced eighteen findings under four ``concerns`` verdicts, none of
    which reached the planner. A verdict above ``ENDORSED`` counts on its own,
    so a reviewer that asks for revision without itemising is still heard.

    ``None`` demands nothing. No panel attached is not an opinion, and looping
    the planner against an opinion nobody offered would spend real money on a
    disagreement that does not exist.

    Args:
        review: The consolidated panel review, or ``None`` when no panel was
            seated or none produced a verdict.

    Returns:
        ``True`` when the plan should go back for another pass.
    """
    if review is None:
        return False
    if review.verdict is not PlanReviewVerdict.ENDORSED:
        return True
    return any(reviewer.findings for reviewer in review.reviewers)


def _review_section(review: PlanReview) -> list[str]:
    """Render the panel's findings, grouped by the reviewer who raised them.

    Returns:
        The rendered lines, empty when no reviewer raised anything.
    """
    lines: list[str] = []
    for reviewer in review.reviewers:
        if not reviewer.findings:
            continue
        lines.append(f"\n{reviewer.reviewer_role} ({reviewer.verdict.value}):")
        lines.extend(
            f"- [{finding.category.value}] {finding.detail}"
            for finding in reviewer.findings
        )
    if lines:
        lines.insert(0, "\nThe stakeholder panel raised these:")
    return lines


def build_revision_brief(*, review: PlanReview | None, note: str | None) -> str:
    """Build the brief appended to the objective for a revision pass.

    Args:
        review: The panel review whose findings must be addressed, if any.
        note: The operator's own change request, if any. Fenced as untrusted
            content: it is human free text arriving at an LLM boundary.

    Returns:
        The brief text.

    Raises:
        ValueError: Neither source carries anything, so there is nothing to
            re-plan against and a pass would spend a round for no reason.
    """
    sections: list[str] = [_HEADER]
    if note is not None and note.strip():
        sections.append("\nThe operator asked for this specifically:")
        sections.append(wrap_untrusted(TAG_TASK_DATA, note.strip()))
    if review is not None:
        sections.extend(_review_section(review))
    if len(sections) == 1:
        msg = "nothing to revise: no operator note and no panel findings"
        raise ValueError(msg)
    sections.append(f"\n{_CLOSING}")
    return "\n".join(sections)
