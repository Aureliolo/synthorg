# module-kind: code
"""Construction of a plan's next revision.

A plan is revised in one of two shapes, and both build a new item set from an
existing plan:

- an **edit**, while the plan is still under review: same entity, bumped
  version, back to pending review;
- a **successor**, once the plan is dispatched: a new entity that supersedes
  the retired revision, because its items are already building and cannot be
  rewritten underneath the tasks implementing them.

Both carry the retired items forward as a diffable snapshot so a reviewer can
see what changed. Kept beside :class:`PlanService` rather than inside it: the
service owns persistence and audit logging, this owns the entity shape.
"""

from datetime import datetime

from pydantic import ValidationError as PydanticValidationError

from synthorg.core.domain_errors import ConflictError
from synthorg.core.plan import (
    MAX_PLAN_VERSION_HISTORY,
    Plan,
    PlanItem,
    PlanVersionSnapshot,
)
from synthorg.core.plan_enums import (
    REPLANNABLE_STATUSES,
    REWORKABLE_STATUSES,
    PlanStatus,
)
from synthorg.core.task_enums import CoordinationTopology, TaskStructure
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_PLAN_TRANSITION_REJECTED

logger = get_logger(__name__)


def require_reworkable(plan: Plan) -> None:
    """Reject an in-place rework of a plan that is no longer under review.

    Raises:
        ConflictError: ``plan.status`` is dispatched or terminal, so its items
            cannot be rewritten in place.
    """
    if plan.status in REWORKABLE_STATUSES:
        return
    logger.warning(
        API_PLAN_TRANSITION_REJECTED,
        plan_id=str(plan.id),
        status=plan.status.value,
        reason="terminal_plan_not_reworkable",
    )
    msg = (
        f"Plan {plan.id} is {plan.status.value} and can no longer be "
        "reworked (a decision has already been recorded)"
    )
    raise ConflictError(msg)


def require_replannable(plan: Plan) -> None:
    """Reject a re-plan of a plan that is not dispatched.

    A re-plan retires the current revision before building its successor, so
    the caller checks this before any write lands.

    Raises:
        ConflictError: ``plan.status`` is not APPROVED or EXECUTING, so it is
            either still editable in place or already terminal.
    """
    if plan.status in REPLANNABLE_STATUSES:
        return
    logger.warning(
        API_PLAN_TRANSITION_REJECTED,
        plan_id=str(plan.id),
        status=plan.status.value,
        reason="not_dispatched_not_replannable",
    )
    msg = (
        f"Plan {plan.id} is {plan.status.value}; only a dispatched plan "
        "(approved or executing) is replanned"
    )
    raise ConflictError(msg)


def snapshot(plan: Plan) -> PlanVersionSnapshot:
    """Freeze a plan's current items as a diffable version snapshot.

    Returns:
        A :class:`PlanVersionSnapshot` capturing *plan*'s version, items, and
        classified structure at its current ``updated_at``.
    """
    return PlanVersionSnapshot(
        version=plan.version,
        items=plan.items,
        task_structure=plan.task_structure,
        captured_at=plan.updated_at,
    )


def describe_validation_error(exc: PydanticValidationError) -> str:
    """Flatten a validation error into one operator-readable line.

    Returns:
        Semicolon-separated ``field: message`` pairs.
    """
    return "; ".join(
        f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()
    )


def extended_history(plan: Plan) -> tuple[PlanVersionSnapshot, ...]:
    """Return *plan*'s version history with its current state appended.

    Returns:
        The history capped at :data:`MAX_PLAN_VERSION_HISTORY` entries, oldest
        dropped first so the JSON column cannot grow unbounded.
    """
    return (*plan.version_history, snapshot(plan))[-MAX_PLAN_VERSION_HISTORY:]


def build_successor(
    existing: Plan,
    *,
    items: tuple[PlanItem, ...],
    task_structure: TaskStructure | None,
    coordination_topology: CoordinationTopology | None,
    # Plain ``datetime`` rather than ``AwareDatetime``: the constrained type is
    # a model-field annotation and typeguard rejects it on a parameter. The
    # Plan model validates tz-awareness when it builds.
    now: datetime,
) -> Plan:
    """Build the revision that replaces a dispatched plan.

    A new entity rather than a version bump: the retired plan stays queryable
    with the items its tasks were built from. The successor re-enters review
    because its items carry no approval, and it inherits the objective and
    framing so the initiative keeps its identity across the revision.

    Returns:
        The unsaved successor plan, awaiting review.

    Raises:
        PydanticValidationError: The revised items violate a plan invariant.
    """
    return Plan(
        project=existing.project,
        objective_id=existing.objective_id,
        objective_title=existing.objective_title,
        parent_task_id=existing.parent_task_id,
        items=items,
        task_structure=task_structure or existing.task_structure,
        coordination_topology=(coordination_topology or existing.coordination_topology),
        status=PlanStatus.PENDING_REVIEW,
        forecast_id=existing.forecast_id,
        # The retired plan's panel judged different items; the successor shows
        # no verdict until its own review runs.
        review=None,
        open_questions=existing.open_questions,
        assumptions=existing.assumptions,
        objective_criteria=existing.objective_criteria,
        version_history=extended_history(existing),
        created_at=now,
        updated_at=now,
    )
