# module-kind: code
"""Build the brief a stalled initiative is replanned against.

The successor is only as good as what the planner is told went wrong, so the
brief names the stall shape, every outstanding item with the status it died in,
and the objective's success criteria (which the successor still has to satisfy).

Item titles and objective criteria are agent-authored or operator-authored text
reaching a planning prompt, so they are fenced with :func:`wrap_untrusted` under
``TAG_TASK_DATA`` (SEC-1); the instructions around the fence are the only
trusted text in the result.
"""

from synthorg.core.plan import Plan
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.initiative.completion import (
    ItemProgress,
    StallReason,
    item_is_done,
)
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted

#: What each stall shape means for the planner, phrased as the constraint the
#: successor has to satisfy rather than as a diagnosis of the retired plan.
_REASON_GUIDANCE: dict[StallReason, str] = {
    StallReason.ALL_FAILED: (
        "Every outstanding item was attempted and did not survive. Re-planning "
        "the same shape will fail the same way: change the approach, split the "
        "work smaller, or route it differently."
    ),
    StallReason.BLOCKED: (
        "Every outstanding item is stuck behind something the organisation "
        "cannot clear on its own. Plan around the blocker, or make removing it "
        "the first item."
    ),
    StallReason.MIXED_DEAD: (
        "Some outstanding items failed and others are blocked. Treat both: the "
        "successor must not inherit either the failing approach or the blocker."
    ),
    StallReason.INTEGRATION_FAILED: (
        "Every piece was built and passed its own review, but assembling them "
        "into one running deliverable failed. The gap is between the pieces, "
        "not inside them: plan the work that makes them fit together, and do "
        "not simply rebuild what already passed."
    ),
    StallReason.EVALUATION_UNMET: (
        "The assembled deliverable runs but does not meet the objective's "
        "success criteria. Plan the work that closes the gap against those "
        "criteria specifically, not another pass at the same scope."
    ),
}


def _progress_lines(plan: Plan, items: tuple[ItemProgress, ...]) -> list[str]:
    """Describe what the retired plan delivered and what it did not.

    Returns:
        One line per plan item, outstanding items carrying the status their
        task died in so the planner can tell a failure from a blockage.
    """
    titles = {subtask_uuid(item.id): item.title for item in plan.items}
    lines: list[str] = []
    for progress in items:
        title = titles.get(progress.item_id, str(progress.item_id))
        if item_is_done(progress):
            lines.append(f"- DELIVERED: {title}")
            continue
        status = (
            progress.task_status.value
            if progress.task_status is not None
            else "never dispatched"
        )
        lines.append(f"- OUTSTANDING ({status}): {title}")
    return lines


def build_replan_brief(
    plan: Plan,
    items: tuple[ItemProgress, ...],
    reason: StallReason,
) -> str:
    """Compose the planning brief for a stalled initiative.

    Returns:
        The brief: trusted framing around a fenced report of the retired plan's
        outcomes and the criteria the successor must still satisfy.
    """
    report = ["Where the previous plan got to:", *_progress_lines(plan, items)]
    if plan.objective_criteria:
        report.append("The objective is met only when all of these hold:")
        report.extend(f"- {criterion}" for criterion in plan.objective_criteria)
    return "\n".join(
        [
            "The previous plan for this objective stopped making progress and "
            "is being replaced. Plan the remaining work from where the "
            "organisation actually is: keep what was delivered, and find "
            "another way to the rest.",
            f"Stall: {reason.value}. {_REASON_GUIDANCE[reason]}",
            wrap_untrusted(TAG_TASK_DATA, "\n".join(report)),
        ]
    )
