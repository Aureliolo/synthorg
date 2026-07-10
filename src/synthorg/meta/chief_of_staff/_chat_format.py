# module-kind: code
"""Pure prompt-context formatters for the Chief of Staff chat.

Extracted from ``chat.py`` so the service module stays well under its
size budget. Each helper renders a readable text block from typed
inputs; the caller in ``chat.py`` owns the untrusted-content fencing
around any block that carries human- or agent-authored fields.
"""

from synthorg.budget.currency import format_cost
from synthorg.core.approval import ApprovalItem
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.org_state import OrgStateSnapshot
from synthorg.meta.models import OrgSignalSnapshot


def free_form_sources(
    snapshot: OrgSignalSnapshot,
    org_state: OrgStateSnapshot | None,
) -> tuple[NotBlankStr, ...]:
    """Compute the provenance-domain tags a free-form answer drew on.

    A domain tag appears only when that surface actually carried data:
    ``performance`` when the snapshot has measured metrics (active
    agents), and ``tasks`` / ``projects`` / ``approvals`` when the org
    state has any of those in flight. Empty when the org-state read model
    is unavailable and there are no measured metrics.

    Returns:
        The domain tags, in a stable order.
    """
    tags: list[NotBlankStr] = []
    if snapshot.performance.agent_count > 0:
        tags.append(NotBlankStr("performance"))
    if org_state is not None:
        if org_state.in_progress_total or org_state.in_review_total:
            tags.append(NotBlankStr("tasks"))
        if org_state.active_projects_total:
            tags.append(NotBlankStr("projects"))
        if org_state.pending_approvals_total:
            tags.append(NotBlankStr("approvals"))
    return tuple(tags)


def format_snapshot(snapshot: OrgSignalSnapshot) -> str:
    """Format a snapshot into a readable summary string.

    When there are no active agents the performance summary is the empty
    sentinel, so its quality / success / collaboration numbers are not
    measured data. They are rendered as an explicit "no measured data"
    line rather than as literal zeros the model might report as a real
    outcome.

    Returns:
        Resulting string.
    """
    perf = snapshot.performance
    budget = snapshot.budget
    coord = snapshot.coordination
    if perf.agent_count > 0:
        lines = [
            f"Quality: {perf.avg_quality_score:.1f}/10",
            f"Success Rate: {perf.avg_success_rate:.0%}",
            f"Collaboration: {perf.avg_collaboration_score:.1f}/10",
            f"Active Agents: {perf.agent_count}",
        ]
    else:
        lines = [
            "Performance metrics: no measured data yet "
            "(no active agents in the trailing window).",
        ]
    lines.extend(
        [
            f"Total Spend: {format_cost(budget.total_spend)}",
            f"Orchestration Overhead: {budget.orchestration_overhead:.2f}",
            f"Error Findings: {snapshot.errors.total_findings}",
        ]
    )
    if coord.coordination_overhead_pct is not None:
        lines.append(
            f"Coordination Overhead: {coord.coordination_overhead_pct:.0%}",
        )
    return "\n".join(lines)


def format_scoped_proposal(item: ApprovalItem) -> str:
    """Format a scoped approval-queue item into readable context.

    ``ApprovalItem`` is the durable projection a proposal survives into
    once parked for human review; it carries title/description plus
    whatever the submitting path recorded in ``metadata`` (altitude,
    source rule), not the full ``ImprovementProposal``.

    Returns:
        Formatted context lines describing the scoped proposal.
    """
    lines = [
        "The user's question is scoped to this pending proposal:",
        f"Title: {item.title}",
        f"Description: {item.description}",
        f"Status: {item.status.value}",
    ]
    altitude = item.metadata.get("altitude")
    if altitude:
        lines.append(f"Altitude: {altitude}")
    source_rule = item.metadata.get("source_rule")
    if source_rule:
        lines.append(f"Source rule: {source_rule}")
    return "\n".join(lines)


def format_signal_context(ctx: dict[str, object]) -> str:
    """Format a signal context dict into readable lines.

    Returns:
        Resulting string.
    """
    return "\n".join(f"{k}: {v}" for k, v in ctx.items())
