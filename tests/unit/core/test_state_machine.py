"""Tests for the generic ``StateMachine`` helper."""

from enum import StrEnum

import pytest

from synthorg.core.state_machine import StateMachine


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

    def test_every_hop_in_path_is_individually_valid(self) -> None:
        machine = _make_machine()
        path = machine.path_to(_Color.RED, _Color.BLUE)
        assert path is not None
        cursor = _Color.RED
        for hop in path:
            machine.validate(cursor, hop)  # raises if any hop is illegal
            cursor = hop
