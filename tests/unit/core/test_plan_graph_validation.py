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
    describe_undecidable_criterion,
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
    acceptance_criteria: tuple[str, ...] = field(default=())
    expected_artifacts: tuple[str, ...] = field(default=())


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


class TestUndecidableCriterion:
    """A gate must be judgeable from evidence that exists when it runs.

    The DAG orders the WORK. It says nothing about whether the EVIDENCE a
    gate demands has been produced by the time that gate is reached, and a
    live run spent 1192 seconds of reviewer time refusing an item whose own
    criterion named a file two waves downstream.
    """

    def test_a_criterion_naming_a_later_item_artefact_is_rejected(self) -> None:
        server = _Unit(
            id="server",
            title="HTTP server",
            expected_artifacts=("server.js", "README.md"),
            acceptance_criteria=(
                "`node server.js` starts and serves index.html with HTTP 200",
            ),
        )
        others = [
            server,
            _Unit(id="board", title="Board markup", dependencies=("logic",)),
            _Unit(
                id="ui",
                title="Game page",
                dependencies=("board",),
                expected_artifacts=("index.html",),
            ),
        ]

        detail = describe_undecidable_criterion(unit=server, others=others)

        assert detail is not None
        assert "'server'" in detail
        assert "index.html" in detail

    def test_a_transitive_dependency_makes_the_artefact_available(self) -> None:
        """The closure, not the direct edges: evidence flows down the whole chain."""
        checks = _Unit(
            id="checks",
            title="Smoke checks",
            dependencies=("server",),
            expected_artifacts=("smoke.js",),
            acceptance_criteria=("the suite loads index.html and asserts a board",),
        )
        others = [
            checks,
            _Unit(
                id="ui",
                title="Game page",
                expected_artifacts=("index.html",),
            ),
            _Unit(id="server", title="HTTP server", dependencies=("ui",)),
        ]

        assert describe_undecidable_criterion(unit=checks, others=others) is None

    def test_an_item_may_be_judged_on_what_it_produces_itself(self) -> None:
        ui = _Unit(
            id="ui",
            title="Game page",
            expected_artifacts=("index.html",),
            acceptance_criteria=("index.html renders a 10x20 board",),
        )
        others = [ui, _Unit(id="other", title="Scores", expected_artifacts=("db.sql",))]

        assert describe_undecidable_criterion(unit=ui, others=others) is None

    def test_a_prose_deliverable_is_not_matched_as_a_file(self) -> None:
        """Otherwise every criterion sharing a plan's noun trips on it."""
        rules = _Unit(
            id="rules",
            title="Rules engine",
            expected_artifacts=("rules.js",),
            acceptance_criteria=("a playable game loop clears a filled row",),
        )
        others = [
            rules,
            _Unit(id="ship", title="Ship it", expected_artifacts=("a playable game",)),
        ]

        assert describe_undecidable_criterion(unit=rules, others=others) is None

    def test_the_same_filename_declared_by_both_is_the_unit_s_own(self) -> None:
        """Two items may each write a README; judging on one's own is decidable."""
        first = _Unit(
            id="first",
            title="Server",
            expected_artifacts=("README.md",),
            acceptance_criteria=("README.md documents how to start it",),
        )
        others = [
            first,
            _Unit(id="second", title="Client", expected_artifacts=("README.md",)),
        ]

        assert describe_undecidable_criterion(unit=first, others=others) is None
