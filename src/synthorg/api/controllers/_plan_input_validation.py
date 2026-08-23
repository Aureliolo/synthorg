# module-kind: service
"""What a hand-authored item list must satisfy before it becomes a plan.

Three controller paths hand the plan service items a person or an agent
wrote: the edit on ``PATCH /plans/{id}``, the successor on
``POST /plans/{id}/replan``, and the re-decomposition behind
``POST /plans/{id}/request-changes``. They share these checks because the
payload model cannot make either of them: one needs the roster to know
whether an owner routes, and the other needs the whole item set to know
whether the graph says what the plan claims.

They live here rather than beside any one caller so that a fourth path
cannot quietly get a different answer, and so that "what an operator may
submit" is one thing to read.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.core.domain_errors import ServiceUnavailableError, ValidationError
from synthorg.core.plan import PlanItem
from synthorg.core.plan_role_validation import describe_unroutable_role
from synthorg.core.plan_validation import (
    ORDERED_STRUCTURES,
    combine_graph_violations,
    describe_structureless_graph,
    describe_undecidable_criteria,
    describe_unstated_references,
)
from synthorg.core.task_enums import TaskStructure
from synthorg.engine.decomposition.context import roster_from_agents
from synthorg.hr.state import HrStateSlice

__all__ = ["reject_undecidable_graph", "reject_unroutable_owners"]


def reject_undecidable_graph(
    items: Sequence[PlanItem],
    *,
    task_structure: TaskStructure | None = None,
) -> None:
    """Refuse hand-authored items whose graph cannot be judged as written.

    The same three questions decomposition is held to, asked at the boundary
    the operator writes through. Their own docstrings promise this, and only
    the LLM path asked them, so a plan edited or replanned by hand could
    declare a structure with no edges, cite an item it never named, or gate
    itself on a file only a non-dependency produces: the last of those is
    unjudgeable when the item is reviewed and stays unjudgeable through every
    rework, which is a gate that refuses for as long as the plan stands.

    Args:
        items: The revised items, as the operator wrote them.
        task_structure: The structure the revision declares, when it names
            one. Absent, the ordering check has nothing to contradict and is
            skipped rather than guessed at.

    Raises:
        ValidationError: The graph fails one of the three checks.
    """
    detail: str | None = None
    if task_structure is not None:
        detail = describe_structureless_graph(
            declared_sequential=task_structure in ORDERED_STRUCTURES,
            units=items,
        )
    if detail is None:
        # Every violation at once, so an operator fixing a revision by hand
        # sees the whole list rather than discovering the next one each time
        # they resubmit.
        detail = combine_graph_violations(
            (
                *describe_unstated_references(items),
                *describe_undecidable_criteria(items),
            )
        )
    if detail is not None:
        raise ValidationError(detail)


async def reject_unroutable_owners(
    app_state: AppState, items: Sequence[PlanItem]
) -> None:
    """Refuse a revision that owns an item to a role the org does not staff.

    Every path that hands the plan service new items runs this, because the
    payload validator is a pure model and cannot see who the org employs: an
    invented owner otherwise reaches review as an item nothing can be
    dispatched to.

    Args:
        app_state: Application state carrying the HR slice.
        items: The revised items.

    Raises:
        ServiceUnavailableError: The agent registry is not wired, so no
            roster exists to check against. Refused rather than passed:
            accepting the revision would assert that its owners route,
            which is exactly what could not be established, and the empty
            roster an unwired registry stands in for would equally reject
            every owner. The error names the wiring gap so an operator is
            not sent looking at their own input for the cause.
        ValidationError: One or more items name a role no agent holds. Every
            offending item is reported together: a revision is edited as a
            whole, so surfacing one owner per attempt would cost the operator
            a round trip per invented name.
    """
    registry = app_state.slice(HrStateSlice).agent_registry
    if registry is None:
        msg = "Owner validation is unavailable: the agent registry is not wired."
        raise ServiceUnavailableError(msg)
    roster = roster_from_agents(await registry.list_active())
    details = [
        detail
        for item in items
        if (
            detail := describe_unroutable_role(
                entity_id=item.id,
                required_role=item.owner,
                available_roles=roster,
            )
        )
        is not None
    ]
    if details:
        raise ValidationError("; ".join(details))
