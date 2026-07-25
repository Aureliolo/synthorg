# module-kind: code
"""Prompt-context formatters and USER-turn assembly for the CoS chat.

Extracted from ``chat.py`` so the service module stays well under its
size budget. The ``format_*`` helpers each render a readable text block
from typed inputs; ``render_free_form_user`` assembles the full,
fully-fenced free-form USER message (reading recent outcomes and
applying the ``<task-data>`` untrusted-content fencing itself, since it
owns the whole turn rather than a single block).
"""

from typing import Final

from synthorg.budget.currency import format_cost
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.meta.chief_of_staff.models import ChatQuery
from synthorg.meta.chief_of_staff.org_state import OrgStateSnapshot, format_org_state
from synthorg.meta.chief_of_staff.prompts import CHAT_QUERY_USER
from synthorg.meta.chief_of_staff.protocol import OutcomeStore
from synthorg.meta.models import OrgSignalSnapshot
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import COS_CHAT_FAILED

logger = get_logger(__name__)

# Rendered into the prompt when the org-state read model could not be built
# (persistence disconnected or the approval store unwired). System-authored,
# so it is NOT fenced as untrusted content; it instructs the model to admit
# it cannot see task / project / approval state rather than infer idleness.
# Deliberately names no specific missing dependency: the operator-facing
# cause (which subsystem is unwired) is carried by the WARNING the request
# helper logs, not baked into the user-facing answer where it could be wrong.
_ORG_STATE_UNAVAILABLE: Final[str] = (
    "The org-state read model is currently unavailable, so I cannot see"
    " task, project, or approval state."
)

# How many recent proposal/alert outcomes to fold into the chat context.
_RECENT_OUTCOMES_LIMIT: Final[int] = 5


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
            (
                "Performance metrics: no measured data yet "
                "(no active agents in the trailing window)."
            ),
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


async def render_free_form_user(
    *,
    outcome_store: OutcomeStore | None,
    query: ChatQuery,
    snapshot: OrgSignalSnapshot,
    scoped_proposal: ApprovalItem | None,
    org_state: OrgStateSnapshot | None,
) -> str:
    """Render the fenced USER message for a free-form question.

    Reads recent outcomes for context (degrading to a placeholder on a
    store read failure) and folds a resolved ``scoped_proposal`` summary
    ahead of them, folds the real org-state block (or the "cannot see
    state" sentinel), then fences every attacker-controllable field in a
    ``<task-data>`` envelope. The org-state records are human/agent-authored,
    so the rendered block is fenced; the unavailable sentinel is
    system-authored and stays unfenced.

    Returns:
        The rendered, fully fenced USER-role message.
    """
    recent_context = "No recent proposals or alerts."
    if outcome_store is not None:
        try:
            recent = await outcome_store.recent_outcomes(limit=_RECENT_OUTCOMES_LIMIT)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # A graceful degrade (falls back to a placeholder), so WARNING
            # not ERROR; carry the redacted error context the sibling
            # provider-failure logs also emit, so the failure is diagnosable.
            logger.warning(
                COS_CHAT_FAILED,
                reason="outcome_store_read_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            recent = ()
        if recent:
            lines = [
                f"- {o.title} ({o.decision}, {o.decided_at:%Y-%m-%d})" for o in recent
            ]
            recent_context = "Recent outcomes:\n" + "\n".join(lines)
    if scoped_proposal is not None:
        scoped = format_scoped_proposal(scoped_proposal)
        recent_context = f"{scoped}\n\n{recent_context}"
    if org_state is not None:
        org_state_block = wrap_untrusted(TAG_TASK_DATA, format_org_state(org_state))
    else:
        org_state_block = _ORG_STATE_UNAVAILABLE
    return CHAT_QUERY_USER.format(
        snapshot_summary=wrap_untrusted(TAG_TASK_DATA, format_snapshot(snapshot)),
        org_state=org_state_block,
        recent_context=wrap_untrusted(TAG_TASK_DATA, recent_context),
        user_question=wrap_untrusted(TAG_TASK_DATA, query.question),
    )
