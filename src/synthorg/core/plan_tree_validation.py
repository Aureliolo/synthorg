# module-kind: code
"""What makes a set of plan units a containment tree.

The sibling of :mod:`synthorg.core.plan_validation`, which owns the invariants
one unit answers for on its own. These are the ones only the whole set can
answer: a parent has to resolve, the parent graph has to reach a workstream,
and a decision cannot contain anything.

Apart from :mod:`synthorg.core.plan_tree`, which derives the VIEWS of a tree
already known to be well formed. The split is also what keeps the import
graph acyclic: ``PlanItem`` calls into this at construction, so this may not
name ``PlanItem``, while the views are built from items and freely do.
"""

from collections.abc import Sequence
from typing import Protocol

from synthorg.core.plan_enums import PlanItemKind


class TreeUnit(Protocol):
    """What the containment invariants read off one plan unit.

    Structural rather than the concrete ``PlanItem``: naming the entity here
    would point this module back at the one that imports it, and the API's
    edit payload satisfies the same shape without being one.
    """

    @property
    def id(self) -> str:
        """Identity of the unit within its plan."""
        ...

    @property
    def parent_id(self) -> str | None:
        """The unit this one was split out of, or ``None`` for a workstream."""
        ...

    @property
    def kind(self) -> PlanItemKind:
        """Whether this unit is work to execute or a decision point."""
        ...

    @property
    def dependencies(self) -> tuple[str, ...]:
        """Ids of the units this one waits for."""
        ...


def describe_malformed_tree(units: Sequence[TreeUnit]) -> tuple[str, ...]:
    """Name every way *units* fail to form a containment tree.

    ``parent_id`` carries structure: what an item belongs to, never when it
    runs. The one place the two graphs meet is the last rule below, and it
    constrains what a dependency may SAY rather than overruling anything it
    says: a level is the unit a dependency is declared within, which was true
    before the tree existed, because ``DecompositionPlan`` already refuses a
    subtask whose dependency is not one of its own level's. A cross-subtree
    need is stated between the containers, which the tree already expresses.

    Every violation is reported rather than the first, because a hand-authored
    plan is corrected in one pass and reporting one fault per submission costs
    a round per fault.

    Args:
        units: The plan's units, in plan order.

    Returns:
        One message per violation, empty when the units form a tree.
    """
    known = {unit.id: unit for unit in units}
    problems: list[str] = []
    for unit in units:
        parent = unit.parent_id
        if parent is None:
            continue
        held = known.get(parent)
        if held is None:
            problems.append(
                f"plan item {unit.id!r} names parent {parent!r}, "
                f"which is not an item of this plan"
            )
            continue
        if held.kind is PlanItemKind.DECISION:
            # A decision is resolved by the reviewer and never dispatched, so
            # a subtree hanging off one could never be assembled: dispatch
            # strips the decision and its children would be orphaned.
            problems.append(
                f"plan item {unit.id!r} hangs off decision {parent!r}; "
                f"a decision is chosen, not decomposed"
            )
    problems.extend(_describe_parent_cycles(units, known))
    problems.extend(_describe_cross_level_dependencies(units, known))
    return tuple(problems)


def _describe_cross_level_dependencies(
    units: Sequence[TreeUnit], known: dict[str, TreeUnit]
) -> tuple[str, ...]:
    """Name each dependency pointing outside the declaring unit's own level.

    Only asked of units whose parent resolves, so an orphan is reported once
    by its own rule rather than again by this one.

    Returns:
        One message per offending edge.
    """
    return tuple(
        f"plan item {unit.id!r} depends on {dependency!r}, which sits at "
        f"another level; a dependency names a unit at the same level"
        for unit in units
        if unit.parent_id is None or unit.parent_id in known
        for dependency in unit.dependencies
        if dependency in known and known[dependency].parent_id != unit.parent_id
    )


def _describe_parent_cycles(
    units: Sequence[TreeUnit], known: dict[str, TreeUnit]
) -> tuple[str, ...]:
    """Name the units whose parent chain never reaches a workstream.

    Kahn's reduction over the parent graph, the same shape the dependency
    cycle check uses: repeatedly drop whatever has no unresolved parent, and
    whatever survives is caught in a cycle. A unit whose parent is unknown is
    dropped rather than reported again, so an orphan is one message and not
    two.

    Returns:
        One message naming the cycle, or empty when the graph is acyclic.
    """
    pending = {
        unit.id: unit.parent_id
        for unit in units
        if unit.parent_id is not None and unit.parent_id in known
    }
    while True:
        settled = {
            unit_id
            for unit_id, parent in pending.items()
            if parent is None or parent not in pending
        }
        if not settled:
            break
        for unit_id in settled:
            del pending[unit_id]
    if not pending:
        return ()
    return (f"plan items form a containment cycle: {sorted(pending)}",)


__all__ = ["TreeUnit", "describe_malformed_tree"]
