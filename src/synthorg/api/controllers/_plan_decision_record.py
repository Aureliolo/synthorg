# module-kind: orchestrator
"""Record an approved plan's decision-items into the project brain.

A plan ``DECISION`` item is resolved by the reviewer's choice, not executed, so
it never dispatches as a build task (``decomposition_from_plan`` strips it).
That resolution is a first-class shaping decision, so on approval each decision
is recorded into the project brain as a queryable ``DECISION`` entry: the
reviewer's explicit pick, or the owner's recommended option when they left it to
the default. Without this the chosen option would vanish at dispatch, leaving no
durable trace of what the company decided.

Best-effort and non-authoritative: it never blocks the dispatch (the work items
build regardless) and quietly no-ops when the brain is not wired, so a
brain-write fault never strands an approved plan.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_FAILED,
)
from synthorg.project_brain.models import (
    BrainEntryStatus,
    DecisionPayload,
)
from synthorg.project_brain.state import ProjectBrainStateSlice

logger = get_logger(__name__)

#: Field bounds from ``project_brain.models`` (BrainTitle / BrainShortText); the
#: option text is clamped to fit so a long option cannot fail model validation
#: and lose the whole plan-decision record.
_TITLE_MAX: int = 512
_TEXT_MAX: int = 4096


async def record_plan_decisions(
    app_state: AppState,
    plan: Plan,
    *,
    decided_by: str,
) -> None:
    """Record each of *plan*'s resolved decision-items as a brain DECISION.

    No-ops when the plan carries no decision items or the brain service is not
    wired. Each decision resolves to the reviewer's chosen option, or the
    owner's recommended one when unchosen (:meth:`PlanItem.resolved_option`).

    Raises:
        MemoryError: Propagated unconditionally (non-recoverable).
        RecursionError: Propagated unconditionally (non-recoverable).
    """
    decisions = tuple(i for i in plan.items if i.kind is PlanItemKind.DECISION)
    if not decisions:
        return
    brain_service = app_state.slice(ProjectBrainStateSlice).service
    if brain_service is None:
        return
    project = NotBlankStr(str(plan.project))
    parent_task_id = NotBlankStr(str(plan.parent_task_id))
    for item in decisions:
        chosen = item.resolved_option()
        if chosen is None:
            continue
        alternatives = tuple(
            NotBlankStr(o.title) for o in item.options if o.id != chosen.id
        )
        try:
            await brain_service.append_entry(
                project_id=project,
                title=NotBlankStr(item.title[:_TITLE_MAX]),
                rationale=NotBlankStr(chosen.summary[:_TEXT_MAX]),
                status=BrainEntryStatus.ACCEPTED,
                author=NotBlankStr(decided_by),
                payload=DecisionPayload(
                    decision_outcome=chosen.title[:_TEXT_MAX],
                    alternatives=alternatives,
                ),
                related_task_ids=(parent_task_id,),
            )
        except Exception as exc:  # noqa: BLE001 -- best-effort: never strand dispatch
            reraise_critical(exc)
            logger.warning(
                APPROVAL_GATE_RESUME_FAILED,
                plan_id=str(plan.id),
                item_id=item.id,
                note="failed to record plan decision in the brain",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
