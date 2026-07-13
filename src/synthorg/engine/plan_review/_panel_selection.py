# module-kind: code
"""Stakeholder review-panel selection for the plan-review phase.

Picks a bounded panel of accountable reviewers for a built plan from the
standing roster: the leads whose lens the plan most needs (a technical lead, a
budget lead, and the department heads for the domains its owners touch) plus a
senior peer, never the plan's own owner (no self-review). The panel is sized to
the coordination group bounds, so 'the whole company reviews it' means the
relevant leads sized to the plan, not everyone.
"""

from collections.abc import Callable
from functools import cmp_to_key

from synthorg.core.agent import AgentIdentity
from synthorg.core.normalization import normalize_identifier
from synthorg.core.role_catalog import get_builtin_role
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.hr.seniority import compare_seniority
from synthorg.observability import get_logger
from synthorg.observability.events.plan_review import (
    PLAN_REVIEW_PANEL_EMPTY,
    PLAN_REVIEW_PANEL_SELECTED,
)

logger = get_logger(__name__)

#: Roles that always sit on the panel when present: the technical lens (CTO)
#: and the budget lens (CFO). Order is the seating priority.
_STANDING_PANEL_ROLES: tuple[str, ...] = ("CTO", "CFO")

_seniority_cmp = cmp_to_key(compare_seniority)


def _seniority_key(agent: AgentIdentity) -> tuple[object, str]:
    """Sort key ranking an agent most-senior-first, ties broken on id.

    Returns:
        A key usable with ``max``: higher seniority sorts first, a stable id
        breaks ties.
    """
    return (_seniority_cmp(agent.level), str(agent.id))


def _most_senior(
    pool: list[AgentIdentity],
    predicate: Callable[[AgentIdentity], bool] | None = None,
) -> AgentIdentity | None:
    """Return the most senior agent in *pool*, optionally filtered.

    Args:
        pool: Candidate agents to choose from.
        predicate: Optional filter; when given, only agents it accepts are
            considered.

    Returns:
        The most senior matching agent (ties broken on a stable id), or
        ``None`` when nothing matches.
    """
    matches = pool if predicate is None else [a for a in pool if predicate(a)]
    if not matches:
        return None
    return max(matches, key=_seniority_key)


def _role_predicate(role_name: str) -> Callable[[AgentIdentity], bool]:
    """Build a predicate matching agents holding *role_name*.

    Returns:
        A predicate true for an agent whose role normalises equal to
        ``role_name`` (case/whitespace-insensitive).
    """
    target = normalize_identifier(role_name)
    return lambda agent: normalize_identifier(agent.role) == target


def _department_predicate(
    dept: str, exclude_ids: set[str]
) -> Callable[[AgentIdentity], bool]:
    """Build a predicate matching not-yet-picked agents in *dept*.

    Returns:
        A predicate true for an agent in department ``dept`` whose id is not in
        ``exclude_ids``.
    """
    return lambda agent: (
        normalize_identifier(agent.department) == dept
        and str(agent.id) not in exclude_ids
    )


def _not_picked_predicate(exclude_ids: set[str]) -> Callable[[AgentIdentity], bool]:
    """Build a predicate matching agents not yet picked.

    Returns:
        A predicate true for an agent whose id is not in ``exclude_ids``.
    """
    return lambda agent: str(agent.id) not in exclude_ids


def _touched_departments(plan: DecompositionResult) -> tuple[str, ...]:
    """Departments the plan's item owners belong to, in first-seen order.

    Each item's owning role is mapped to its built-in department; unknown
    (custom) roles contribute no department. Order-preserving and de-duplicated
    so the panel seats one lead per distinct domain the plan actually touches.

    Returns:
        The distinct department names (normalised) the plan touches.
    """
    seen: list[str] = []
    for subtask in plan.plan.subtasks:
        role_name = subtask.required_role
        if role_name is None:
            continue
        role = get_builtin_role(role_name)
        if role is None:
            continue
        dept = normalize_identifier(role.department.value)
        if dept not in seen:
            seen.append(dept)
    return tuple(seen)


def select_review_panel(
    plan: DecompositionResult,
    agents: tuple[AgentIdentity, ...],
    *,
    owner: AgentIdentity | None,
    limit: int,
) -> tuple[AgentIdentity, ...]:
    """Assemble a bounded stakeholder panel to review *plan*.

    Seats, in priority order and de-duplicated by agent id: a technical lead
    (CTO) and a budget lead (CFO) when present; the most senior agent in each
    domain department the plan's owners touch; then the most senior remaining
    agents (senior peers) until ``limit`` is reached. The plan's own owner is
    always excluded so no one reviews their own plan.

    Args:
        plan: The built plan whose owners' domains size the panel.
        agents: The active roster to seat the panel from.
        owner: The plan's owner, excluded from the panel (no self-review).
        limit: Maximum panel size (the coordination group bound).

    Returns:
        The panel as a tuple of :class:`AgentIdentity` (at most ``limit``),
        empty when the roster has no eligible reviewer or ``limit`` is
        non-positive.
    """
    owner_id = str(owner.id) if owner is not None else None
    candidates = [a for a in agents if str(a.id) != owner_id]
    if not candidates or limit <= 0:
        logger.info(
            PLAN_REVIEW_PANEL_EMPTY, candidate_count=len(candidates), limit=limit
        )
        return ()

    picked: list[AgentIdentity] = []
    picked_ids: set[str] = set()

    def take(agent: AgentIdentity | None) -> None:
        if agent is None or str(agent.id) in picked_ids or len(picked) >= limit:
            return
        picked.append(agent)
        picked_ids.add(str(agent.id))

    for role_name in _STANDING_PANEL_ROLES:
        take(_most_senior(candidates, _role_predicate(role_name)))
    for dept in _touched_departments(plan):
        take(_most_senior(candidates, _department_predicate(dept, picked_ids)))
    while len(picked) < limit:
        peer = _most_senior(candidates, _not_picked_predicate(picked_ids))
        if peer is None:
            break
        take(peer)

    panel = tuple(picked)
    logger.info(
        PLAN_REVIEW_PANEL_SELECTED,
        panel_size=len(panel),
        reviewer_roles=[a.role for a in panel],
        limit=limit,
    )
    return panel
