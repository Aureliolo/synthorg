# module-kind: code
"""The deterministic autonomy gate a slice ask crosses before grafting.

Split out from :mod:`synthorg.engine.initiative.slice_graft` for the same
reason :mod:`synthorg.engine.initiative.slice_state` was: kept apart so both
modules stay within their module-size tier.
"""

from collections.abc import Awaitable, Callable

from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.plan import Plan
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import INITIATIVE_SLICE_SETTINGS_DEGRADED
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.autonomy.enums import ActionType
from synthorg.security.autonomy.resolver import AutonomyResolver

logger = get_logger(__name__)

#: Resolves the autonomy governing *plan*'s own initiative-level scope
#: decisions, as ``ReplanTriggerService._effective_autonomy`` already does.
#: Not the per-run ``AutonomyResolution`` seam: a slice ask is the
#: organisation acting on its own plan, not an agent's tool call, so there is
#: no run or identity to resolve it for. ``None`` means it could not be
#: determined (no resolver wired, or the project row is gone), which fails
#: closed to the deterministic gate applying.
EffectiveAutonomyForPlan = Callable[[Plan], Awaitable[EffectiveAutonomy | None]]


async def resolve_effective_autonomy_for_plan(
    plan: Plan,
    *,
    persistence: PersistenceBackend,
    autonomy_resolver: AutonomyResolver | None,
) -> EffectiveAutonomy | None:
    """Resolve the autonomy governing *plan*'s own initiative-level scope.

    Not the per-run ``AutonomyResolution`` seam: a slice ask is the org
    acting on its own plan, not an agent's tool call, so ``project_level``
    alone (no agent or department override) is the input.

    Returns:
        The resolved autonomy, or ``None`` when no resolver is wired, the
        project no longer exists, or resolution itself failed.
    """
    if autonomy_resolver is None:
        return None
    project = await persistence.projects.get(plan.project)
    project_level = project.autonomy_mode if project is not None else None
    try:
        return autonomy_resolver.resolve(project_level=project_level)
    except ValueError as exc:
        logger.warning(
            INITIATIVE_SLICE_SETTINGS_DEGRADED,
            key="autonomy_resolver",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def auto_approved(resolve: EffectiveAutonomyForPlan, plan: Plan) -> bool:
    """Whether *plan*'s effective autonomy auto-approves extending a workstream.

    The only sound semantic, matching ``ConnectionApprovalGate``: an action
    type is auto-approved by explicit membership in ``auto_approve_actions``,
    never by its mere absence from ``human_approval_actions``. Reading the
    absence the other way auto-grafts under any preset that never names
    ``plan:extend_workstream`` in either set, which is every preset but LOCKED
    and FULL as shipped.

    An autonomy that could not be resolved fails closed to the gate applying:
    a missing resolver or a deleted project row is not evidence that a person
    already agreed to this.

    Returns:
        ``True`` only when the concrete action type is explicitly granted.
    """
    effective = await resolve(plan)
    if effective is None:
        return False
    return ActionType.PLAN_EXTEND_WORKSTREAM.value in effective.auto_approve_actions


__all__ = [
    "EffectiveAutonomyForPlan",
    "auto_approved",
    "resolve_effective_autonomy_for_plan",
]
