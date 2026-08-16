# module-kind: service
"""A plan's decision items, recorded on the plan at the moment they resolve.

An approved decision always resolves to a concrete option: the reviewer's pick
if they made one, otherwise the owner's recommendation (``resolved_option``).
Dispatch relies on that -- ``decomposition_from_plan`` strips every decision id
out of the work items' dependencies on the stated grounds that "the decision is
already made by approval time".

An unwritten resolution leaves two readers free to disagree about the same
decision on the same plan: dispatch reads it as MADE and releases every item
that depended on it, while completion reads ``chosen_option_id is None`` and
reports the item as NOT DONE (``initiative/completion.py::item_is_done``). An
initiative carrying a decision the operator did not explicitly click could
then never finish, whatever its work items did.

So the resolution is written to ``chosen_option_id`` before anything
dispatches, and that field is the single owner of what was decided. The
project brain is a consumer of it rather than the only place it exists, which
matters because the brain is an optional subsystem: unwired, it would record
an implicit resolution nowhere at all.
"""

from synthorg.core.clock import Clock
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.pipeline import PIPELINE_PLAN_DECISION_RESOLVED
from synthorg.persistence.plan_protocol import PlanRepository

logger = get_logger(__name__)


def _resolved_item(item: PlanItem) -> PlanItem | None:
    """Return *item* with its resolution recorded, or ``None`` if unchanged.

    Returns:
        A copy carrying ``chosen_option_id``, or ``None`` when the item is not
        an unresolved decision.
    """
    if item.kind is not PlanItemKind.DECISION or item.chosen_option_id is not None:
        return None
    option = item.resolved_option()
    if option is None:
        return None
    return item.model_copy(update={"chosen_option_id": NotBlankStr(option.id)})


async def record_resolved_decisions(
    plans: PlanRepository,
    plan: Plan,
    *,
    clock: Clock,
) -> Plan:
    """Write each decision item's resolved option onto *plan*, then persist it.

    Runs on the approval path, before the work items are rebuilt into a task
    tree. Approving without picking an option IS a decision -- to take the
    recommendation -- and this is where that becomes a fact the plan carries
    rather than one recomputed differently by whoever asks next.

    Args:
        plans: Repository holding the durable plan.
        plan: The approved plan.
        clock: Supplies the update timestamp.

    Returns:
        The persisted plan when anything resolved, otherwise *plan* unchanged.
    """
    resolutions = {
        item.id: resolved
        for item in plan.items
        if (resolved := _resolved_item(item)) is not None
    }
    if not resolutions:
        return plan
    updated = plan.model_copy(
        update={
            "items": tuple(resolutions.get(item.id, item) for item in plan.items),
            "updated_at": clock.now(),
        }
    )
    await plans.update(updated, expected_version=plan.version)
    for item in resolutions.values():
        logger.info(
            PIPELINE_PLAN_DECISION_RESOLVED,
            plan_id=str(plan.id),
            item_id=item.id,
            chosen_option_id=item.chosen_option_id,
            chosen_by="recommendation",
        )
    return await plans.get(NotBlankStr(str(plan.id))) or updated
