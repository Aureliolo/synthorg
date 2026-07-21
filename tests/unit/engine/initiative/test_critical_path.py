"""Tests for the plan critical-path computation."""

import pytest

from synthorg.engine.initiative.critical_path import longest_dependency_chain

pytestmark = pytest.mark.unit


class TestLongestDependencyChain:
    """The chain that sets the delivery date, in dependency order."""

    def test_linear_chain_is_the_whole_plan(self) -> None:
        deps = {"a": (), "b": ("a",), "c": ("b",)}
        assert longest_dependency_chain(deps) == ("a", "b", "c")

    def test_picks_the_longer_of_two_branches(self) -> None:
        # a -> b -> d, and a -> c (shorter): the long branch wins.
        deps = {"a": (), "b": ("a",), "c": ("a",), "d": ("b",)}
        assert longest_dependency_chain(deps) == ("a", "b", "d")

    def test_diamond_resolves_through_the_longer_side(self) -> None:
        #     b -> c
        #  a <        > d
        #     e ------->
        deps = {
            "a": (),
            "b": ("a",),
            "c": ("b",),
            "e": ("a",),
            "d": ("c", "e"),
        }
        assert longest_dependency_chain(deps) == ("a", "b", "c", "d")

    def test_independent_items_yield_a_single_step(self) -> None:
        deps = {"a": (), "b": (), "c": ()}
        chain = longest_dependency_chain(deps)
        assert len(chain) == 1
        assert chain[0] in {"a", "b", "c"}

    def test_empty_plan_has_no_critical_path(self) -> None:
        assert longest_dependency_chain({}) == ()

    def test_ties_break_deterministically(self) -> None:
        """Two equal-length chains must not flip between calls."""
        deps = {"a": (), "b": ("a",), "x": (), "y": ("x",)}
        assert longest_dependency_chain(deps) == longest_dependency_chain(deps)

    def test_unknown_dependency_is_ignored(self) -> None:
        """A dependency stripped at dispatch (a decision) is not a node."""
        deps = {"b": ("gone",), "c": ("b",)}
        assert longest_dependency_chain(deps) == ("b", "c")

    def test_a_cycle_does_not_hang(self) -> None:
        """Plan validation rejects cycles, but this must stay total."""
        deps = {"a": ("b",), "b": ("a",)}
        assert longest_dependency_chain(deps) == ()
