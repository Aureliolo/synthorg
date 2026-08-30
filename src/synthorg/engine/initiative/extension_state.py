# module-kind: code
"""Pure derivation of whether a workstream needs another extension.

Kept apart from :mod:`synthorg.engine.initiative.completion` and
:mod:`synthorg.engine.initiative.rollup_stages` for the same reason the
latter is its own module: a focused, pure-derivation unit stays within its
module-size tier on its own.

The question this answers is deliberately narrower than "is the workstream's
objective met": it asks whether the workstream's currently-known tree is
entirely done AND at least one of its leaves was dispatched despite the
atomicity policy finding it still oversized when a backstop stopped its
split (:attr:`~synthorg.core.plan.PlanItem.unsplit_reason`). That is a
deterministic, already-persisted fact, not a judgement about delivered
quality, so nothing here needs a judged check.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Final

from synthorg.core.plan import PlanItem
from synthorg.core.plan_tree import PlanTree
from synthorg.engine.initiative.completion import ItemProgress, item_is_done


class ExtensionDisposition(StrEnum):
    """What the extension trigger did with a workstream it was asked to consider.

    Mirrors :class:`~synthorg.engine.initiative.completion.ReplanDisposition`
    on the same shape: "may a workstream that finished its planned tree
    without covering its objective be handed another extension unasked" is
    the trigger's decision, so a caller reading its mere presence as "an
    extension will be planned" is a second authority that cannot see either
    refusal.

    ``GRAFTED``: a detached graft started. ``ALREADY_RUNNING``: one is in
    flight for this workstream, so this ask collapses into it.
    ``UNAVAILABLE``: the trigger could not start work at this moment.

    ``ASKED``: a deterministic gate applies, so one decision was parked and
    nothing was grafted; only a human answering it can graft the next
    extension for this workstream, and refusing it ends the workstream with
    its scope unmet. ``DISABLED`` and ``BUDGET_EXHAUSTED`` mean no automatic
    route remains for this workstream, on the same two guards
    ``ReplanDisposition`` carries.
    """

    GRAFTED = "grafted"
    ALREADY_RUNNING = "already_running"
    UNAVAILABLE = "unavailable"
    ASKED = "asked"
    DISABLED = "disabled"
    BUDGET_EXHAUSTED = "budget_exhausted"


#: Dispositions where something is happening or will happen without anyone
#: being asked, mirroring ``REPLAN_IN_PROGRESS_DISPOSITIONS``.
EXTENSION_IN_PROGRESS_DISPOSITIONS: Final[frozenset[ExtensionDisposition]] = frozenset(
    {
        ExtensionDisposition.GRAFTED,
        ExtensionDisposition.ALREADY_RUNNING,
        ExtensionDisposition.UNAVAILABLE,
    }
)

#: Dispositions where no automatic route remains for this leaf: the switch is
#: off, or the generation cap is spent. Kept apart from
#: ``EXTENSION_IN_PROGRESS_DISPOSITIONS`` so the two constants, plus
#: ``ASKED``, partition every member ``consider_extension`` can answer; a
#: caller checking membership in both before treating a disposition as
#: refused catches a future member landing in neither, rather than silently
#: falling through as refused.
EXTENSION_REFUSED_DISPOSITIONS: Final[frozenset[ExtensionDisposition]] = frozenset(
    {ExtensionDisposition.DISABLED, ExtensionDisposition.BUDGET_EXHAUSTED}
)


def leaf_needs_extension(item: PlanItem, progress: ItemProgress) -> bool:
    """Whether *item* is a completed leaf whose claimed scope may be incomplete.

    ``unsplit_reason`` is written by the decomposition projection only when
    ``SubtaskAtomicityPolicy.assess`` found the unit still oversized, whether
    by declaring too many expected artifacts or acceptance criteria, or by
    claiming more of the plan's objective criteria than one unit should, and
    a backstop (depth, session, or turn budget) stopped it being split
    further. Such a unit was still dispatched as one leaf, so its completion
    does not mean one agent's turn actually covered everything it claimed;
    the field exists to record that gap for exactly this question.

    Returns:
        ``True`` when the item completed but was never atomic.
    """
    return item.unsplit_reason is not None and item_is_done(progress)


def workstream_needs_extension(
    plan_items: tuple[PlanItem, ...],
    tree: PlanTree,
    workstream: PlanItem,
    progress_by_id: Mapping[str, ItemProgress],
) -> tuple[PlanItem, ...]:
    """Which of *workstream*'s completed leaves still need another extension.

    Only asked once the whole subtree is done: a workstream carrying a live
    item is still moving, and one carrying a dead item is a stall, and
    neither question is this one to answer. A subtree with an item this call
    has no progress for reads as not-done, the same carve-out
    ``_work_item_is_dead`` makes for a task not yet persisted.

    Args:
        plan_items: The plan's items, in plan order.
        tree: The plan's containment view.
        workstream: The top-level item whose subtree is being asked.
        progress_by_id: Live progress for every item, keyed by item id.

    Returns:
        The oversized-and-completed leaves, in plan order, or empty when the
        workstream is not yet fully done or every leaf was properly atomic.
    """
    subtree = tree.subtree_ids(workstream.id)
    subtree_items = [item for item in plan_items if item.id in subtree]
    subtree_progress = [
        progress_by_id[item.id] for item in subtree_items if item.id in progress_by_id
    ]
    if len(subtree_progress) != len(subtree_items) or not all(
        item_is_done(progress) for progress in subtree_progress
    ):
        return ()
    return tuple(
        item
        for item in subtree_items
        if not tree.is_container(item.id)
        and leaf_needs_extension(item, progress_by_id[item.id])
    )


def workstream_extension_generation(
    plan_items: tuple[PlanItem, ...], tree: PlanTree, workstream: PlanItem
) -> int:
    """How many extensions *workstream* has already received.

    Derived rather than stored: grafting an extension under a leaf gives it
    children, so a formerly-oversized leaf that is now a container is
    exactly one extension having landed. Counting the workstream's
    descendants that are both a container AND still carry ``unsplit_reason``
    (the field is never cleared once written) is therefore the generation,
    with nothing new to persist. The graft's own trailing assembly child
    (see :mod:`synthorg.engine.initiative.extension_graft`) never carries
    ``unsplit_reason``, so it cannot inflate this count.

    Returns:
        How many of the workstream's descendants have been extended.
    """
    subtree = tree.subtree_ids(workstream.id)
    return sum(
        1
        for item in plan_items
        if item.id in subtree
        and item.unsplit_reason is not None
        and tree.is_container(item.id)
    )


__all__ = [
    "EXTENSION_IN_PROGRESS_DISPOSITIONS",
    "EXTENSION_REFUSED_DISPOSITIONS",
    "ExtensionDisposition",
    "leaf_needs_extension",
    "workstream_extension_generation",
    "workstream_needs_extension",
]
