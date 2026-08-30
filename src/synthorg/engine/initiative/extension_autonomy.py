# module-kind: code
"""The deterministic autonomy gate an extension ask crosses before grafting.

Split out from :mod:`synthorg.engine.initiative.extension_graft`, on the same
reasoning that keeps :mod:`synthorg.engine.initiative.extension_state` its own
module rather than folded into a caller: each stays within its own
module-size tier on its own.
"""

from collections.abc import Awaitable, Callable

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.plan import Plan
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import (
    INITIATIVE_EXTENSION_SETTINGS_DEGRADED,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.security.autonomy.enums import ActionType
from synthorg.security.autonomy.resolver import AutonomyResolver

logger = get_logger(__name__)

#: Resolves the autonomy governing *plan*'s own initiative-level scope
#: decisions, as ``ReplanTriggerService._effective_autonomy`` already does.
#: Not the per-run ``AutonomyResolution`` seam: an extension ask is the
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

    Not the per-run ``AutonomyResolution`` seam: an extension ask is the org
    acting on its own plan, not an agent's tool call, so ``project_level``
    alone (no agent or department override) is the input.

    Never raises: a project lookup that fails is read the same as a project
    that no longer exists, both falling back to no resolved level, on the
    same reasoning ``resolve_extension_enabled``'s settings read degrades to its
    default rather than letting a failed dependency stop this from answering.

    Returns:
        The resolved autonomy, or ``None`` when no resolver is wired, the
        project lookup failed or found nothing, or resolution itself failed.
    """
    if autonomy_resolver is None:
        return None
    project_level = await _project_autonomy_level(plan, persistence=persistence)
    try:
        return autonomy_resolver.resolve(project_level=project_level)
    except ValueError as exc:
        logger.warning(
            INITIATIVE_EXTENSION_SETTINGS_DEGRADED,
            key="autonomy_resolver",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def _project_autonomy_level(
    plan: Plan, *, persistence: PersistenceBackend
) -> AutonomyLevel | None:
    """The plan's project's own autonomy mode, or ``None`` when unreadable.

    Guarded so a persistence outage degrades to "no project-level override"
    rather than raising through a caller whose own docstring promises never
    to: the deterministic gate this feeds already fails closed on ``None``,
    so a read that cannot complete is answered the same as a project that
    genuinely carries no override.

    Returns:
        The project's ``autonomy_mode``, or ``None``.
    """
    try:
        project = await persistence.projects.get(plan.project)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort read; the gate fails closed
        reraise_critical(exc)
        logger.warning(
            INITIATIVE_EXTENSION_SETTINGS_DEGRADED,
            key="project_autonomy_mode",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    return project.autonomy_mode if project is not None else None


async def auto_approved(resolve: EffectiveAutonomyForPlan, plan: Plan) -> bool:
    """Whether *plan*'s effective autonomy auto-approves extending a workstream.

    The only sound semantic, matching ``ConnectionApprovalGate``: an action
    type is auto-approved by explicit membership in ``auto_approve_actions``,
    never by its mere absence from ``human_approval_actions``. This matters
    here specifically: every shipped preset accounts for
    ``plan:extend_workstream`` somewhere, but not the same way. SEMI and
    SUPERVISED name it explicitly under ``human_approval`` (for operator
    legibility, even though the semantic below already parks anything absent
    from ``auto_approve``); LOCKED and FULL cover it only through their
    wildcard ``"all"`` entry, never by name. Reading a bare absence from
    ``human_approval_actions`` as auto-approval would misread that wildcard
    coverage as silence and auto-graft under it.

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
