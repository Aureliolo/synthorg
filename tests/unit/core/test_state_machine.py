"""Tests for the generic ``StateMachine`` helper."""

from enum import StrEnum
from typing import ClassVar

import pytest

from synthorg.core.state_machine import HopRules, StateMachine


class _Color(StrEnum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


_TRANSITIONS = {
    _Color.RED: frozenset({_Color.GREEN}),
    _Color.GREEN: frozenset({_Color.BLUE, _Color.RED}),
    _Color.BLUE: frozenset(),  # terminal
}


def _make_machine() -> StateMachine[_Color]:
    return StateMachine(
        _TRANSITIONS,
        name="color",
        invalid_event="test.color.invalid",
        config_event="test.color.config_error",
        all_states=_Color,
    )


@pytest.mark.unit
class TestStateMachineValidation:
    """Happy-path transitions pass silently."""

    def test_valid_transition_does_not_raise(self) -> None:
        machine = _make_machine()
        machine.validate(_Color.RED, _Color.GREEN)

    def test_invalid_transition_raises(self) -> None:
        machine = _make_machine()
        with pytest.raises(ValueError, match="Invalid color transition"):
            machine.validate(_Color.RED, _Color.BLUE)

    def test_terminal_state_blocks_all(self) -> None:
        machine = _make_machine()
        with pytest.raises(ValueError, match="Invalid color transition"):
            machine.validate(_Color.BLUE, _Color.RED)


@pytest.mark.unit
class TestStateMachineCoverageCheck:
    """``all_states`` catches stale transition tables at construction time."""

    def test_missing_entry_raises(self) -> None:
        incomplete = {
            _Color.RED: frozenset({_Color.GREEN}),
            _Color.GREEN: frozenset({_Color.BLUE}),
            # _Color.BLUE missing
        }
        with pytest.raises(ValueError, match="missing transition entries"):
            StateMachine(
                incomplete,
                name="color",
                invalid_event="x",
                config_event="y",
                all_states=_Color,
            )

    def test_skip_coverage_when_all_states_omitted(self) -> None:
        incomplete = {_Color.RED: frozenset({_Color.GREEN})}
        # No all_states kwarg => no coverage check
        StateMachine(
            incomplete,
            name="color",
            invalid_event="x",
            config_event="y",
        )


@pytest.mark.unit
class TestStateMachineHelpers:
    """``allowed`` + ``is_terminal`` helpers."""

    def test_allowed_returns_frozenset(self) -> None:
        machine = _make_machine()
        assert machine.allowed(_Color.RED) == frozenset({_Color.GREEN})

    def test_allowed_unknown_state_raises_keyerror(self) -> None:
        machine = _make_machine()
        with pytest.raises(KeyError):
            machine.allowed("not-a-color")  # type: ignore[arg-type]

    def test_is_terminal_detects_empty_frozenset(self) -> None:
        machine = _make_machine()
        assert machine.is_terminal(_Color.BLUE) is True
        assert machine.is_terminal(_Color.RED) is False


@pytest.mark.unit
class TestStateMachineDisplayLabel:
    """``display_label`` controls the user-visible error message."""

    def test_defaults_to_name_with_spaces(self) -> None:
        machine = StateMachine(
            {_Color.RED: frozenset({_Color.GREEN})},
            name="my_state",
            invalid_event="x",
            config_event="y",
        )
        with pytest.raises(ValueError, match="Invalid my state transition"):
            machine.validate(_Color.RED, _Color.BLUE)

    def test_explicit_display_label_wins(self) -> None:
        machine = StateMachine(
            {_Color.RED: frozenset({_Color.GREEN})},
            name="my_state",
            display_label="My Fancy Label",
            invalid_event="x",
            config_event="y",
        )
        with pytest.raises(ValueError, match="Invalid My Fancy Label transition"):
            machine.validate(_Color.RED, _Color.BLUE)


@pytest.mark.unit
class TestStateMachinePathTo:
    """``path_to`` returns the shortest valid hop sequence (BFS)."""

    def test_same_state_is_empty_path(self) -> None:
        machine = _make_machine()
        assert machine.path_to(_Color.RED, _Color.RED) == ()

    def test_single_hop(self) -> None:
        machine = _make_machine()
        assert machine.path_to(_Color.RED, _Color.GREEN) == (_Color.GREEN,)

    def test_multi_hop_is_shortest(self) -> None:
        machine = _make_machine()
        # RED -> GREEN -> BLUE is the only (and shortest) route.
        assert machine.path_to(_Color.RED, _Color.BLUE) == (
            _Color.GREEN,
            _Color.BLUE,
        )

    def test_terminal_source_is_unreachable(self) -> None:
        machine = _make_machine()
        assert machine.path_to(_Color.BLUE, _Color.RED) is None

    def test_unknown_source_is_none(self) -> None:
        machine: StateMachine[_Color] = StateMachine(
            {_Color.RED: frozenset({_Color.GREEN})},
            name="color",
            invalid_event="x",
            config_event="y",
        )
        # GREEN has no table entry -> no path can originate from it.
        assert machine.path_to(_Color.GREEN, _Color.RED) is None


@pytest.mark.unit
class TestStateMachineNoTransitStates:
    """A park is a destination, never a corridor."""

    class _Park(StrEnum):
        OPEN = "open"
        PARKED = "parked"
        RUNNING = "running"
        DONE = "done"

    _TABLE: ClassVar[dict[_Park, frozenset[_Park]]] = {
        _Park.OPEN: frozenset({_Park.PARKED, _Park.RUNNING}),
        _Park.PARKED: frozenset({_Park.DONE, _Park.OPEN}),
        _Park.RUNNING: frozenset({_Park.DONE}),
        _Park.DONE: frozenset(),
    }

    def _machine(self, *, guarded: bool) -> StateMachine[_Park]:
        return StateMachine(
            self._TABLE,
            name="park",
            invalid_event="x",
            config_event="y",
            all_states=self._Park,
            hops=HopRules(no_transit_states=(self._Park.PARKED,) if guarded else ()),
        )

    def test_a_park_is_not_walked_through_on_the_way_somewhere_else(self) -> None:
        """Without the guard, the shorter route runs through the park.

        Walking an entity through a park records a park that never happened,
        and lands a status whose meaning depends on a reason no walker sets:
        a rule written for the real park then applies to a transit hop.
        """
        assert self._machine(guarded=False).path_to(
            self._Park.OPEN, self._Park.DONE
        ) == (self._Park.PARKED, self._Park.DONE)
        assert self._machine(guarded=True).path_to(
            self._Park.OPEN, self._Park.DONE
        ) == (self._Park.RUNNING, self._Park.DONE)

    def test_a_park_is_still_reachable_as_a_destination(self) -> None:
        assert self._machine(guarded=True).path_to(
            self._Park.OPEN, self._Park.PARKED
        ) == (self._Park.PARKED,)

    def test_a_walk_may_still_start_at_a_park(self) -> None:
        # The escalated-review answer rejoins from the park it waited in, so
        # the guard must bound transit only, never the source.
        assert self._machine(guarded=True).path_to(
            self._Park.PARKED, self._Park.DONE
        ) == (self._Park.DONE,)

    def test_no_route_avoiding_the_park_is_no_route(self) -> None:
        """Fail closed rather than silently falling back through the park."""
        machine: StateMachine[TestStateMachineNoTransitStates._Park] = StateMachine(
            {
                self._Park.OPEN: frozenset({self._Park.PARKED}),
                self._Park.PARKED: frozenset({self._Park.DONE}),
                self._Park.RUNNING: frozenset(),
                self._Park.DONE: frozenset(),
            },
            name="park",
            invalid_event="x",
            config_event="y",
            all_states=self._Park,
            hops=HopRules(no_transit_states=(self._Park.PARKED,)),
        )
        assert machine.path_to(self._Park.OPEN, self._Park.DONE) is None

    def test_a_tie_between_equal_length_routes_is_broken_by_declaration_order(
        self,
    ) -> None:
        """A shortest path is not unique, so the machine must choose one.

        BFS explored successors in ``frozenset`` order, which is derived from
        member hashes and therefore randomised per process. With two routes of
        equal length the answer changed between runs of the same code: the
        walk a task takes through its lifecycle, the statuses its audit trail
        records, and which of them a caller must be able to satisfy all became
        a function of ``PYTHONHASHSEED``.

        Declaration order is the tie-break because an enum declares its
        lifecycle's forward progression first, so the ordinary route wins over
        a detour through a parked state.
        """

        class _Route(StrEnum):
            START = "start"
            WORKING = "working"
            PARKED = "parked"
            DONE = "done"

        machine = StateMachine(
            {
                _Route.START: frozenset({_Route.WORKING, _Route.PARKED}),
                _Route.WORKING: frozenset({_Route.DONE}),
                _Route.PARKED: frozenset({_Route.DONE}),
                _Route.DONE: frozenset(),
            },
            name="route",
            invalid_event="x",
            config_event="y",
            all_states=_Route,
        )

        assert machine.successors(_Route.START) == (_Route.WORKING, _Route.PARKED)
        assert machine.path_to(_Route.START, _Route.DONE) == (
            _Route.WORKING,
            _Route.DONE,
        )

    def test_an_exit_through_an_undeclared_state_is_not_an_exit(self) -> None:
        """A state the table never defines cannot be evidence of a terminal.

        The absent-state guard covered only the state the walk STARTS from, so
        a successor that is unconditional but has no table entry was queued,
        dequeued, found to have no successors, and reported as terminal. The
        lifecycle gate reads this answer to decide that every state can be
        finished or cancelled, so a false ``True`` here is how a stuck status
        ships: the entity reaches a status nothing can move it out of, and the
        gate that exists to catch exactly that says it is fine.
        """

        class _Partial(StrEnum):
            START = "start"
            GHOST = "ghost"

        machine = StateMachine(
            # ``GHOST`` is a declared target with no entry of its own.
            {_Partial.START: frozenset({_Partial.GHOST})},
            name="partial",
            invalid_event="x",
            config_event="y",
            hops=HopRules(unconditional_targets={_Partial.GHOST}),
        )

        assert machine.unconditional_exit_reachable(_Partial.START) is False

    def test_a_declared_terminal_is_still_an_exit(self) -> None:
        """The fix must not refuse the ordinary case it sits next to.

        An empty frozenset is a state the table DOES cover and declares
        terminal, which is the shape every real machine ends on.
        """

        class _Complete(StrEnum):
            START = "start"
            DONE = "done"

        machine = StateMachine(
            {_Complete.START: frozenset({_Complete.DONE}), _Complete.DONE: frozenset()},
            name="complete",
            invalid_event="x",
            config_event="y",
            all_states=_Complete,
            hops=HopRules(unconditional_targets={_Complete.DONE}),
        )

        assert machine.unconditional_exit_reachable(_Complete.START) is True

    def test_successors_of_a_terminal_state_are_empty(self) -> None:
        machine = _make_machine()
        assert machine.successors(_Color.BLUE) == ()

    def test_successors_of_an_unknown_state_are_empty(self) -> None:
        """The walk asks about states the table may not cover; it must not raise."""
        machine: StateMachine[_Color] = StateMachine(
            {_Color.RED: frozenset({_Color.GREEN})},
            name="color",
            invalid_event="x",
            config_event="y",
        )
        assert machine.successors(_Color.BLUE) == ()

    def test_every_hop_in_path_is_individually_valid(self) -> None:
        machine = _make_machine()
        path = machine.path_to(_Color.RED, _Color.BLUE)
        assert path is not None
        cursor = _Color.RED
        for hop in path:
            machine.validate(cursor, hop)  # raises if any hop is illegal
            cursor = hop

    def test_cyclic_graph_terminates_with_shortest_path(self) -> None:
        """A cyclic transition table must not loop forever in BFS.

        ``seen`` guards revisits, so an explicit A<->B cycle still
        terminates and yields the minimal-length path. A regression
        here would hang the suite, not just fail an assertion.
        """

        class _Node(StrEnum):
            A = "a"
            B = "b"
            C = "c"

        cyclic = StateMachine(
            {
                _Node.A: frozenset({_Node.B}),
                _Node.B: frozenset({_Node.A, _Node.C}),
                _Node.C: frozenset(),
            },
            name="node",
            invalid_event="test.node.invalid",
            config_event="test.node.config_error",
            all_states=_Node,
        )

        assert cyclic.path_to(_Node.A, _Node.C) == (_Node.B, _Node.C)
        assert cyclic.path_to(_Node.B, _Node.A) == (_Node.A,)
        assert cyclic.path_to(_Node.A, _Node.A) == ()
        assert cyclic.path_to(_Node.C, _Node.A) is None
