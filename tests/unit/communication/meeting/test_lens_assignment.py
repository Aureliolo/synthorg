"""Lens assignment degrades to no lenses rather than to a bad mapping.

The happy path is covered where the assigner is wired
(``tests/unit/api/test_auto_wire_meetings.py``). What matters here is the
three ways an assigner can fail: consumers look each participant up by
their own id and skip anyone absent, so a partially-wrong mapping would
drop lenses silently instead of reporting anything.
"""

import pytest

from synthorg.communication.meeting._lens_assignment import (
    LensAssigner,
    LensStrategyConfig,
    compute_lens_assignments,
)

pytestmark = pytest.mark.unit

_PARTICIPANTS: tuple[str, ...] = ("agent-a", "agent-b")
_LENSES: tuple[str, ...] = ("risk", "opportunity")


class _Config:
    """Minimal ``LensStrategyConfig`` supplying a fixed lens collection."""

    @property
    def default_lenses(self) -> tuple[str, ...]:
        return _LENSES


class _Assigner:
    """``LensAssigner`` returning whatever it was constructed with."""

    def __init__(self, result: object) -> None:
        self._result = result

    def assign(
        self,
        participant_ids: tuple[str, ...],
        available_lenses: tuple[str, ...],
    ) -> dict[str, str]:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result  # type: ignore[return-value]


def _compute(result: object) -> dict[str, str] | None:
    return compute_lens_assignments(
        _PARTICIPANTS,
        assigner=_Assigner(result),
        strategy_config=_Config(),
    )


class TestLensesDisabled:
    """Either dependency missing turns lenses off without complaint."""

    def test_no_assigner(self) -> None:
        assert (
            compute_lens_assignments(
                _PARTICIPANTS,
                assigner=None,
                strategy_config=_Config(),
            )
            is None
        )

    def test_no_strategy_config(self) -> None:
        assert (
            compute_lens_assignments(
                _PARTICIPANTS,
                assigner=_Assigner({}),
                strategy_config=None,
            )
            is None
        )


class TestAssignerProducesAUsableMapping:
    """A well-formed mapping is returned as its own dict."""

    def test_valid_mapping_is_returned(self) -> None:
        result = _compute({"agent-a": "risk", "agent-b": "opportunity"})

        assert result == {"agent-a": "risk", "agent-b": "opportunity"}

    def test_the_result_is_copied(self) -> None:
        """Mutating the assigner's dict afterwards must not leak in."""
        source = {"agent-a": "risk", "agent-b": "opportunity"}

        result = _compute(source)
        source["agent-a"] = "mutated"

        assert result is not None
        assert result["agent-a"] == "risk"


class TestAssignerFailures:
    """Every unusable answer degrades to ``None``."""

    def test_assigner_raising_degrades(self) -> None:
        assert _compute(RuntimeError("assigner exploded")) is None

    def test_a_critical_error_still_propagates(self) -> None:
        """Degrading is for assigner faults, not for interpreter limits."""
        with pytest.raises(MemoryError):
            _compute(MemoryError())

    def test_missing_participant_degrades(self) -> None:
        assert _compute({"agent-a": "risk"}) is None

    def test_unexpected_participant_degrades(self) -> None:
        assert (
            _compute(
                {"agent-a": "risk", "agent-b": "opportunity", "agent-c": "cost"},
            )
            is None
        )

    def test_renamed_participant_degrades(self) -> None:
        """Right size, wrong keys: the count check alone would pass this."""
        assert _compute({"agent-a": "risk", "agent-z": "opportunity"}) is None

    def test_non_mapping_degrades(self) -> None:
        assert _compute(["risk", "opportunity"]) is None

    def test_empty_lens_degrades(self) -> None:
        assert _compute({"agent-a": "risk", "agent-b": ""}) is None

    def test_non_string_lens_degrades(self) -> None:
        assert _compute({"agent-a": "risk", "agent-b": 42}) is None


class TestProtocolsMatchTheDoubles:
    """The structural types accept these stand-ins, so the test is honest."""

    def test_config_satisfies_the_protocol(self) -> None:
        assert isinstance(_Config(), LensStrategyConfig)

    def test_assigner_satisfies_the_protocol(self) -> None:
        assert isinstance(_Assigner({}), LensAssigner)
