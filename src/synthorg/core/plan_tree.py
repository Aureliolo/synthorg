# module-kind: code
"""The derived views of a plan's containment tree.

``PlanItem.parent_id`` is the only stored fact about structure. Everything a
reader wants from it (which items are workstreams, what hangs off an item, how
deep an item sits, what order assembles bottom-up) is derived here and nowhere
else, so "this item was split" cannot drift from "this item has children" the
way a declared flag would.

Built once per read rather than answered per call: every question below is a
lookup against the same two maps, and a caller holding a plan asks several.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import NamedTuple, Self

from synthorg.core.plan import PlanItem


class SubtreeStep(NamedTuple):
    """One hop down a plan's containment, as an assembly address reads it.

    Attributes:
        title: The container's own title, which is what makes anything derived
            from an address readable to the operator who has to find it.
        position: Where it sits among its siblings, which keeps two siblings
            whose titles sanitise to the same thing apart. Not named ``index``,
            which a :class:`NamedTuple` inherits from ``tuple`` as a method.
    """

    title: str
    position: int


@dataclass(frozen=True, slots=True)
class PlanTree:
    """A plan's items indexed by identity and by containment.

    Attributes:
        by_id: Every item, keyed by its own id.
        by_parent: Each container's id mapped to its children, in plan order.
            A workstream's parent is absent rather than keyed under ``None``,
            so a lookup for a leaf and a lookup for a workstream answer the
            same way.
    """

    by_id: Mapping[str, PlanItem]
    by_parent: Mapping[str, tuple[PlanItem, ...]]

    @classmethod
    def of(cls, items: Sequence[PlanItem]) -> Self:
        """Index *items* into a tree view.

        The items are trusted to form a tree: ``Plan`` refuses an
        unresolvable parent and a containment cycle at construction, so this
        never has to re-ask. A parent naming nothing is simply absent from
        ``by_parent`` and its child reads as a workstream.

        Args:
            items: The plan's items, in plan order.

        Returns:
            The tree view.
        """
        children: dict[str, list[PlanItem]] = {}
        for item in items:
            if item.parent_id is not None:
                children.setdefault(item.parent_id, []).append(item)
        return cls(
            by_id=MappingProxyType({item.id: item for item in items}),
            by_parent=MappingProxyType(
                {parent: tuple(kids) for parent, kids in children.items()}
            ),
        )

    @property
    def workstreams(self) -> tuple[PlanItem, ...]:
        """The items nothing contains: the plan's coarse independent tracks.

        Returns:
            Each parentless item, in plan order.
        """
        return tuple(item for item in self.by_id.values() if item.parent_id is None)

    def children(self, item_id: str) -> tuple[PlanItem, ...]:
        """What was split out of *item_id*.

        Returns:
            Its children in plan order, empty for a leaf.
        """
        return self.by_parent.get(item_id, ())

    def is_container(self, item_id: str) -> bool:
        """Whether *item_id* is the assembly of the work below it.

        Derived from having children rather than declared, which is what stops
        the answer drifting from what the tree actually holds.

        Returns:
            ``True`` when anything names *item_id* as its parent.
        """
        return bool(self.by_parent.get(item_id))

    def parent_id(self, item_id: str) -> str | None:
        """What *item_id* was split out of.

        Returns:
            The parent's id, or ``None`` for a workstream or an unknown id.
        """
        item = self.by_id.get(item_id)
        return None if item is None else item.parent_id

    def depth(self, item_id: str) -> int:
        """How many levels sit above *item_id*.

        Zero-based, so a workstream is at 0 and its children at 1. Counted by
        walking to the root rather than stored, for the same reason
        :meth:`is_container` is derived.

        Returns:
            The level, or ``0`` for an unknown id.
        """
        level = 0
        current = self.parent_id(item_id)
        while current is not None:
            level += 1
            current = self.parent_id(current)
        return level

    def address(self, item_id: str) -> tuple[SubtreeStep, ...]:
        """Where *item_id* sits in the tree, root-first.

        The chain of sibling positions IS a unique address in a tree, which a
        position among siblings alone is not: two containers under different
        parents share theirs, so anything namespaced on one collides with its
        cousin and the two overwrite each other.

        Returns:
            One step per level, from the workstream down to *item_id*; empty
            for an unknown id.
        """
        chain: list[SubtreeStep] = []
        current = self.by_id.get(item_id)
        while current is not None:
            siblings = (
                self.workstreams
                if current.parent_id is None
                else self.children(current.parent_id)
            )
            chain.append(
                SubtreeStep(
                    title=str(current.title),
                    position=next(
                        (
                            at
                            for at, sibling in enumerate(siblings)
                            if sibling.id == current.id
                        ),
                        0,
                    ),
                )
            )
            current = (
                None if current.parent_id is None else self.by_id.get(current.parent_id)
            )
        return tuple(reversed(chain))

    def subtree_ids(self, item_id: str) -> frozenset[str]:
        """Every id at or below *item_id*.

        Returns:
            The subtree's ids, including *item_id* itself.
        """
        found: set[str] = set()
        frontier = [item_id]
        while frontier:
            current = frontier.pop()
            if current in found:
                continue
            found.add(current)
            frontier.extend(child.id for child in self.children(current))
        return frozenset(found)

    def deepest_first(self) -> tuple[PlanItem, ...]:
        """Every item, children before their parent.

        The order an assembly walk needs: a container cannot be assembled
        before what it assembles exists.

        Returns:
            The items, each preceded by its whole subtree.
        """
        ordered: list[PlanItem] = []
        for workstream in self.workstreams:
            ordered.extend(self._below(workstream))
        return tuple(ordered)

    def _below(self, item: PlanItem) -> tuple[PlanItem, ...]:
        """*item*'s subtree, children before it.

        Walked on an explicit stack rather than by recursion, because the
        depth here is the plan's containment depth and a plan may hold a
        thousand items: a chain of them is a tree the validator accepts and
        the interpreter's own limit refuses, so the recursive form turned a
        valid plan into a ``RecursionError``.

        Returns:
            The subtree in assembly order.
        """
        ordered: list[PlanItem] = []
        # The flag says whether this node's children are already on the stack,
        # which is what turns a pre-order walk into the post-order one an
        # assembly needs: a node is emitted on its SECOND visit, by which time
        # everything below it has been emitted.
        pending: list[tuple[PlanItem, bool]] = [(item, False)]
        while pending:
            node, expanded = pending.pop()
            if expanded:
                ordered.append(node)
                continue
            pending.append((node, True))
            # Reversed because a stack returns them backwards, and siblings
            # are emitted in plan order.
            pending.extend((child, False) for child in reversed(self.children(node.id)))
        return tuple(ordered)


__all__ = ["PlanTree", "SubtreeStep"]
