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
from typing import Self

from synthorg.core.plan import PlanItem


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

        Returns:
            The subtree in assembly order.
        """
        return (
            *(node for child in self.children(item.id) for node in self._below(child)),
            item,
        )


__all__ = ["PlanTree"]
