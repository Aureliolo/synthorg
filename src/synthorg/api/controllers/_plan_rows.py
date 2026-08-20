# module-kind: code
"""Composing the plan rows the dashboard reads.

Every plan the controller returns carries the same two resolved references,
the owners named on its items and the decision waiting on it, and each one is
a read the browser must never make for itself. Composed here so a route added
later inherits both rather than deciding again whether to resolve them.
"""

from collections.abc import Sequence

from synthorg.api._plan_decisions import pending_plan_decisions
from synthorg.api._read_names import agent_name_map
from synthorg.api.dto_named_rows import PlanRow, plan_rows
from synthorg.api.state import AppState
from synthorg.core.plan import Plan


async def plan_row(app_state: AppState, plan: Plan) -> PlanRow:
    """Compose the row for one plan.

    Returns:
        The plan with its owners named and its pending decision resolved.
    """
    return PlanRow.of(
        plan,
        await agent_name_map(app_state),
        await pending_plan_decisions(app_state, (str(plan.id),)),
    )


async def plan_page(app_state: AppState, plans: Sequence[Plan]) -> tuple[PlanRow, ...]:
    """Compose the rows for one page of plans.

    Both reads run once across the page rather than once per row, which is
    what keeps a fifty-plan response the same cost as a one-plan response.

    Returns:
        The rows, in order.
    """
    names = await agent_name_map(app_state)
    decisions = await pending_plan_decisions(
        app_state, (str(plan.id) for plan in plans)
    )
    return plan_rows(plans, names, decisions)


__all__ = ["plan_page", "plan_row"]
