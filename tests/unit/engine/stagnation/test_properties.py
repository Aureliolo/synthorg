"""Property-based tests for stagnation detection (Hypothesis)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import JsonValue

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.loop_helpers import compute_fingerprints
from synthorg.engine.stagnation.detector import ToolRepetitionDetector
from synthorg.engine.stagnation.models import StagnationConfig, StagnationVerdict
from synthorg.execution.turn import TurnRecord
from synthorg.providers.models import ToolCall


def _turn(
    turn_number: int,
    fingerprints: tuple[str, ...] = (),
) -> TurnRecord:
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=10,
        output_tokens=5,
        cost=0.001,
        tool_call_fingerprints=fingerprints,
        finish_reason=FinishReason.TOOL_USE if fingerprints else FinishReason.STOP,
    )


# ── Fingerprint properties ──────────────────────────────────────────

_json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=5)
        | st.dictionaries(st.text(max_size=10), children, max_size=5)
    ),
    max_leaves=10,
)

_args_strategy = st.dictionaries(
    st.text(min_size=1, max_size=10),
    _json_values,
    max_size=5,
)

# Tool names use NotBlankStr -- filter out whitespace-only strings
_tool_name = st.text(min_size=1, max_size=20).filter(lambda s: s.strip())


@pytest.mark.unit
class TestFingerprintProperties:
    """Property-based tests for fingerprint computation."""

    @given(
        name=_tool_name,
        args=_args_strategy,
    )
    def test_determinism(
        self,
        name: str,
        args: dict[str, JsonValue],
    ) -> None:
        """Same name + args always produces the same fingerprint."""
        tc = ToolCall(id="tc-1", name=name, arguments=args)
        fp1 = compute_fingerprints((tc,))
        fp2 = compute_fingerprints((tc,))
        assert fp1 == fp2

    @given(
        name=_tool_name,
        args=_args_strategy,
    )
    def test_format(
        self,
        name: str,
        args: dict[str, JsonValue],
    ) -> None:
        """Each fingerprint ends with :16-char-hex."""
        tc = ToolCall(id="tc-1", name=name, arguments=args)
        fps = compute_fingerprints((tc,))
        assert len(fps) == 1
        # rsplit to handle tool names containing ':'
        prefix, _, hash_part = fps[0].rpartition(":")
        assert prefix == name
        assert len(hash_part) == 16


# ── Detector properties ──────────────────────────────────────────────


@pytest.mark.unit
class TestDetectorProperties:
    """Property-based tests for the ToolRepetitionDetector."""

    @given(
        n_unique=st.integers(min_value=2, max_value=10),
    )
    async def test_unique_fingerprints_no_stagnation(
        self,
        n_unique: int,
    ) -> None:
        """All-unique fingerprints never trigger stagnation."""
        turns = tuple(_turn(i + 1, (f"tool_{i}:{'0' * 16}",)) for i in range(n_unique))
        detector = ToolRepetitionDetector(
            StagnationConfig(
                window_size=n_unique,
                min_tool_turns=2,
                cycle_detection=False,
            ),
        )
        result = await detector.check(turns)
        assert result.verdict == StagnationVerdict.NO_STAGNATION

    @given(
        window_size=st.integers(min_value=2, max_value=20),
        n_turns=st.integers(min_value=5, max_value=50),
    )
    async def test_window_bounded(
        self,
        window_size: int,
        n_turns: int,
    ) -> None:
        """Detector never considers more turns than window_size."""
        # All unique fingerprints -- no stagnation possible
        turns = tuple(_turn(i + 1, (f"t{i}:{'0' * 16}",)) for i in range(n_turns))
        detector = ToolRepetitionDetector(
            StagnationConfig(
                window_size=window_size,
                min_tool_turns=2,
                cycle_detection=False,
            ),
        )
        result = await detector.check(turns)
        # With all unique fingerprints and no cycles, should be no stagnation
        assert result.verdict == StagnationVerdict.NO_STAGNATION
