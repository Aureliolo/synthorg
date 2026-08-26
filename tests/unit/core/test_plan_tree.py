"""Unit tests for the plan item tree: its invariants and its derived views."""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.core.plan import Plan, PlanItem, PlanOption
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.plan_tree import PlanTree, SubtreeStep
from synthorg.core.plan_tree_validation import describe_malformed_tree
from synthorg.core.types import NotBlankStr
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.unit

_CREATED_AT = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


def _item(
    label: str,
    *,
    parent: str | None = None,
    dependencies: tuple[str, ...] = (),
) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Item {label}"),
        description=NotBlankStr(f"Description for {label}"),
        parent_id=NotBlankStr(sid(parent)) if parent is not None else None,
        dependencies=tuple(NotBlankStr(d) for d in dependencies),
        acceptance_criteria=(NotBlankStr(f"{label} is done"),),
        expected_artifacts=(NotBlankStr(f"src/{label}.py"),),
    )


def _decision(label: str, *, parent: str | None = None) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(sid(label)),
        title=NotBlankStr(f"Decision {label}"),
        description=NotBlankStr(f"Choose for {label}"),
        parent_id=NotBlankStr(sid(parent)) if parent is not None else None,
        acceptance_criteria=(NotBlankStr("the choice is recorded"),),
        kind=PlanItemKind.DECISION,
        options=(
            PlanOption(
                id=NotBlankStr("opt-a"),
                title=NotBlankStr("A"),
                summary=NotBlankStr("The first way"),
                recommended=True,
            ),
            PlanOption(
                id=NotBlankStr("opt-b"),
                title=NotBlankStr("B"),
                summary=NotBlankStr("The second way"),
            ),
        ),
    )


def _plan(items: tuple[PlanItem, ...]) -> Plan:
    return Plan(
        id=as_uuid("plan"),
        project=NotBlankStr("beachhead"),
        project_name=NotBlankStr("Games"),
        objective_id=NotBlankStr("obj"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr("root"),
        items=items,
        status=PlanStatus.DRAFT,
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


#: engine, its two leaves, ui, and a leaf under ui.
_THREE_LEVELS: tuple[PlanItem, ...] = (
    _item("engine"),
    _item("ui"),
    _item("board", parent="engine"),
    _item("rotation", parent="engine"),
    _item("render", parent="ui"),
    _item("sprite", parent="render"),
)


class TestTreeInvariants:
    def test_accepts_a_flat_plan(self) -> None:
        assert describe_malformed_tree((_item("a"), _item("b"))) == ()

    def test_accepts_a_tree(self) -> None:
        assert describe_malformed_tree(_THREE_LEVELS) == ()

    def test_rejects_an_unresolvable_parent(self) -> None:
        problems = describe_malformed_tree((_item("a", parent="ghost"),))
        assert len(problems) == 1
        assert "not an item of this plan" in problems[0]

    def test_rejects_a_parent_cycle(self) -> None:
        # a -> b -> a. Neither is a self-parent, so the item-level rule cannot
        # see it; only walking the parent graph can.
        problems = describe_malformed_tree(
            (_item("a", parent="b"), _item("b", parent="a"))
        )
        assert len(problems) == 1
        assert "cycle" in problems[0]

    def test_rejects_a_decision_as_a_parent(self) -> None:
        problems = describe_malformed_tree(
            (_decision("stack"), _item("impl", parent="stack"))
        )
        assert len(problems) == 1
        assert "decision" in problems[0].lower()

    def test_reports_every_violation_not_just_the_first(self) -> None:
        problems = describe_malformed_tree(
            (
                _item("a", parent="ghost"),
                _item("b", parent="phantom"),
            )
        )
        assert len(problems) == 2

    def test_plan_refuses_an_unresolvable_parent(self) -> None:
        with pytest.raises(ValueError, match="not an item of this plan"):
            _plan((_item("a", parent="ghost"),))

    def test_plan_refuses_a_parent_cycle(self) -> None:
        with pytest.raises(ValueError, match="cycle"):
            _plan((_item("a", parent="b"), _item("b", parent="a")))

    def test_plan_refuses_a_decision_parent(self) -> None:
        with pytest.raises(ValueError, match="decision"):
            _plan((_decision("stack"), _item("impl", parent="stack")))

    def test_item_refuses_being_its_own_parent(self) -> None:
        with pytest.raises(ValueError, match="own parent"):
            PlanItem(
                id=NotBlankStr(sid("a")),
                title=NotBlankStr("X"),
                description=NotBlankStr("Y"),
                parent_id=NotBlankStr(sid("a")),
                acceptance_criteria=(NotBlankStr("done"),),
                expected_artifacts=(NotBlankStr("src/x.py"),),
            )

    def test_accepts_a_dependency_between_siblings(self) -> None:
        assert (
            describe_malformed_tree(
                (
                    _item("engine"),
                    _item("board", parent="engine"),
                    _item("rotation", parent="engine", dependencies=(sid("board"),)),
                )
            )
            == ()
        )

    def test_rejects_a_dependency_across_levels(self) -> None:
        # A level is the unit a dependency is declared within, and that was
        # true before the tree existed: DecompositionPlan refuses a subtask
        # whose dependency is not one of its own level's. A cross-subtree need
        # is stated between the containers, which the tree already expresses.
        problems = describe_malformed_tree(
            (
                _item("engine"),
                _item("ui"),
                _item("board", parent="engine"),
                _item("render", parent="ui", dependencies=(sid("board"),)),
            )
        )
        assert len(problems) == 1
        assert "same level" in problems[0]


class TestPlanTree:
    def test_workstreams_are_the_parentless_items(self) -> None:
        tree = PlanTree.of(_THREE_LEVELS)
        assert [item.id for item in tree.workstreams] == [sid("engine"), sid("ui")]

    def test_children_are_in_item_order(self) -> None:
        tree = PlanTree.of(_THREE_LEVELS)
        assert [item.id for item in tree.children(sid("engine"))] == [
            sid("board"),
            sid("rotation"),
        ]

    def test_a_leaf_has_no_children(self) -> None:
        tree = PlanTree.of(_THREE_LEVELS)
        assert tree.children(sid("board")) == ()
        assert not tree.is_container(sid("board"))

    def test_is_container_is_derived_from_having_children(self) -> None:
        tree = PlanTree.of(_THREE_LEVELS)
        assert tree.is_container(sid("engine"))
        assert tree.is_container(sid("render"))

    def test_depth_counts_levels_from_zero(self) -> None:
        tree = PlanTree.of(_THREE_LEVELS)
        assert tree.depth(sid("engine")) == 0
        assert tree.depth(sid("board")) == 1
        assert tree.depth(sid("sprite")) == 2

    def test_subtree_ids_include_the_root_of_the_subtree(self) -> None:
        tree = PlanTree.of(_THREE_LEVELS)
        assert tree.subtree_ids(sid("ui")) == frozenset(
            {sid("ui"), sid("render"), sid("sprite")}
        )

    def test_deepest_first_puts_every_child_before_its_parent(self) -> None:
        tree = PlanTree.of(_THREE_LEVELS)
        order = [item.id for item in tree.deepest_first()]
        for item in _THREE_LEVELS:
            if item.parent_id is not None:
                assert order.index(item.id) < order.index(item.parent_id)

    def test_parent_id_answers_none_for_a_workstream(self) -> None:
        tree = PlanTree.of(_THREE_LEVELS)
        assert tree.parent_id(sid("engine")) is None
        assert tree.parent_id(sid("board")) == sid("engine")

    def test_an_empty_plan_has_an_empty_tree(self) -> None:
        tree = PlanTree.of(())
        assert tree.workstreams == ()
        assert tree.deepest_first() == ()

    def test_a_chain_as_deep_as_a_plan_is_large_still_orders(self) -> None:
        # The backend accepts a thousand items (`_MAX_ITEMS` in dto_plans.py)
        # and nothing stops them forming one chain, so the containment depth
        # reaches the interpreter's own recursion limit. A walk that recursed
        # per level answered this valid plan with a RecursionError.
        depth = 1000
        chain = tuple(
            _item(f"n{index}", parent=None if index == 0 else f"n{index - 1}")
            for index in range(depth)
        )

        order = [item.id for item in PlanTree.of(chain).deepest_first()]

        assert len(order) == depth
        # Deepest first, so the chain comes back exactly reversed.
        assert order == [item.id for item in reversed(chain)]


class TestAddress:
    def test_a_workstream_is_one_step(self) -> None:
        tree = PlanTree.of(_THREE_LEVELS)
        assert tree.address(sid("engine")) == (
            SubtreeStep(title="Item engine", position=0),
        )

    def test_a_nested_item_carries_its_whole_chain(self) -> None:
        tree = PlanTree.of(_THREE_LEVELS)
        assert tree.address(sid("sprite")) == (
            SubtreeStep(title="Item ui", position=1),
            SubtreeStep(title="Item render", position=0),
            SubtreeStep(title="Item sprite", position=0),
        )

    def test_cousins_at_the_same_sibling_position_get_different_addresses(
        self,
    ) -> None:
        # The whole reason the address is the chain rather than the position:
        # both of these sit first under their own parent, so anything derived
        # from the position alone puts them in one place.
        items = (
            _item("front"),
            _item("back"),
            _item("setup-a", parent="front"),
            _item("setup-b", parent="back"),
        )
        tree = PlanTree.of(items)
        assert tree.address(sid("setup-a")) != tree.address(sid("setup-b"))

    def test_an_unknown_id_has_no_address(self) -> None:
        assert PlanTree.of(_THREE_LEVELS).address(sid("ghost")) == ()


@st.composite
def _valid_trees(draw: st.DrawFn) -> tuple[PlanItem, ...]:
    """Build an arbitrary well-formed item tree.

    Each item may only parent to one already emitted, which makes a cycle
    unconstructible and keeps the strategy generating rather than filtering.
    """
    size = draw(st.integers(min_value=1, max_value=12))
    items: list[PlanItem] = []
    for index in range(size):
        parent = draw(st.sampled_from([None, *range(index)])) if index else None
        items.append(
            _item(f"n{index}", parent=None if parent is None else f"n{parent}")
        )
    return tuple(items)


class TestPlanTreeProperties:
    @given(items=_valid_trees())
    def test_any_valid_tree_orders_children_before_parents(
        self, items: tuple[PlanItem, ...]
    ) -> None:
        tree = PlanTree.of(items)
        order = [item.id for item in tree.deepest_first()]
        assert len(order) == len(items)
        for item in items:
            if item.parent_id is not None:
                assert order.index(item.id) < order.index(item.parent_id)

    @given(items=_valid_trees())
    def test_any_valid_tree_is_accepted(self, items: tuple[PlanItem, ...]) -> None:
        assert describe_malformed_tree(items) == ()
