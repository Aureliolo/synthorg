"""A plan's graph must not contradict what the plan says about itself.

Both checks lock a live failure: six items came back declaring a ``mixed``
structure, zero dependency edges, and an "Integrate game loop: tie engine,
renderer, and input together" item that named the three items it was free to
run before.
"""

from dataclasses import dataclass, field

import pytest

from synthorg.core.plan_validation import (
    describe_structureless_graph,
    describe_unstated_reference,
)

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _Unit:
    """A plan unit as the graph invariants read it."""

    id: str
    title: str
    description: str = ""
    dependencies: tuple[str, ...] = field(default=())


class TestStructurelessGraph:
    def test_an_ordered_structure_with_no_edges_is_rejected(self) -> None:
        detail = describe_structureless_graph(
            declared_sequential=True,
            units=[
                _Unit(id="a", title="Engine"),
                _Unit(id="b", title="Renderer"),
            ],
        )

        assert detail is not None
        assert "no dependencies at all" in detail

    def test_one_declared_edge_satisfies_the_declaration(self) -> None:
        detail = describe_structureless_graph(
            declared_sequential=True,
            units=[
                _Unit(id="a", title="Engine"),
                _Unit(id="b", title="Renderer", dependencies=("a",)),
            ],
        )

        assert detail is None

    def test_a_parallel_plan_needs_no_edges(self) -> None:
        detail = describe_structureless_graph(
            declared_sequential=False,
            units=[_Unit(id="a", title="Engine"), _Unit(id="b", title="Renderer")],
        )

        assert detail is None

    def test_a_single_item_plan_has_no_ordering_to_contradict(self) -> None:
        detail = describe_structureless_graph(
            declared_sequential=True,
            units=[_Unit(id="a", title="Engine")],
        )

        assert detail is None


class TestUnstatedReference:
    def test_an_item_naming_another_without_depending_on_it_is_rejected(self) -> None:
        integrate = _Unit(
            id="int",
            title="Integrate game loop",
            description="Tie the collision engine and the sprite renderer together",
        )
        others = [
            integrate,
            _Unit(id="eng", title="Collision engine"),
            _Unit(id="ren", title="Sprite renderer"),
        ]

        detail = describe_unstated_reference(unit=integrate, others=others)

        assert detail is not None
        assert "'int'" in detail

    def test_a_declared_dependency_clears_the_reference(self) -> None:
        integrate = _Unit(
            id="int",
            title="Integrate game loop",
            description="Tie the collision engine and the sprite renderer together",
            dependencies=("eng", "ren"),
        )
        others = [
            integrate,
            _Unit(id="eng", title="Collision engine"),
            _Unit(id="ren", title="Sprite renderer"),
        ]

        assert describe_unstated_reference(unit=integrate, others=others) is None

    def test_shared_generic_vocabulary_is_not_a_reference(self) -> None:
        """Otherwise every plan trips on its own verbs."""
        unit = _Unit(id="a", title="Build the API", description="Create endpoints")
        others = [unit, _Unit(id="b", title="Build the docs")]

        assert describe_unstated_reference(unit=unit, others=others) is None

    def test_a_partial_token_overlap_is_not_a_reference(self) -> None:
        unit = _Unit(
            id="a",
            title="Collision engine",
            description="Detect overlaps between sprites",
        )
        others = [unit, _Unit(id="b", title="Sprite renderer pipeline")]

        assert describe_unstated_reference(unit=unit, others=others) is None
