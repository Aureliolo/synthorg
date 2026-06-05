"""Tests for the ToolRepetitionDetector."""

import pytest

from synthorg.engine.stagnation.detector import ToolRepetitionDetector
from synthorg.engine.stagnation.models import (
    StagnationConfig,
    StagnationVerdict,
)
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import FinishReason


def _turn(
    turn_number: int,
    fingerprints: tuple[str, ...] = (),
) -> TurnRecord:
    """Build a TurnRecord with given fingerprints."""
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=10,
        output_tokens=5,
        cost=0.001,
        tool_calls_made=tuple(fp.split(":")[0] for fp in fingerprints),
        tool_call_fingerprints=fingerprints,
        finish_reason=FinishReason.TOOL_USE if fingerprints else FinishReason.STOP,
    )


@pytest.mark.unit
class TestToolRepetitionDetectorNoStagnation:
    """Cases that should return NO_STAGNATION."""

    async def test_empty_turns(self) -> None:
        detector = ToolRepetitionDetector()
        result = await detector.check(())
        assert result.verdict == StagnationVerdict.NO_STAGNATION

    async def test_unique_fingerprints(self) -> None:
        turns = tuple(_turn(i + 1, (f"tool_{i}:hash_{i:016x}",)) for i in range(5))
        detector = ToolRepetitionDetector()
        result = await detector.check(turns)
        assert result.verdict == StagnationVerdict.NO_STAGNATION

    async def test_below_min_tool_turns(self) -> None:
        turns = (_turn(1, ("search:abc1234567890123",)),)
        detector = ToolRepetitionDetector(
            StagnationConfig(min_tool_turns=2),
        )
        result = await detector.check(turns)
        assert result.verdict == StagnationVerdict.NO_STAGNATION

    async def test_turns_without_tool_calls(self) -> None:
        turns = tuple(_turn(i + 1) for i in range(10))
        detector = ToolRepetitionDetector()
        result = await detector.check(turns)
        assert result.verdict == StagnationVerdict.NO_STAGNATION

    async def test_disabled(self) -> None:
        """Config enabled=False should always return NO_STAGNATION."""
        turns = tuple(_turn(i + 1, ("search:same_hash_12345",)) for i in range(10))
        detector = ToolRepetitionDetector(
            StagnationConfig(enabled=False),
        )
        result = await detector.check(turns)
        assert result.verdict == StagnationVerdict.NO_STAGNATION

    async def test_below_threshold(self) -> None:
        """Repetition ratio below threshold."""
        turns = (
            _turn(1, ("search:hash_a_123456789",)),
            _turn(2, ("search:hash_b_123456789",)),
            _turn(3, ("search:hash_c_123456789",)),
            _turn(4, ("search:hash_d_123456789",)),
            _turn(5, ("search:hash_a_123456789",)),  # 1 duplicate
        )
        # 1 duplicate out of 5 = 0.2, below default 0.6
        detector = ToolRepetitionDetector(
            StagnationConfig(cycle_detection=False),
        )
        result = await detector.check(turns)
        assert result.verdict == StagnationVerdict.NO_STAGNATION


@pytest.mark.unit
class TestToolRepetitionDetectorInjectPrompt:
    """Cases that should return INJECT_PROMPT."""

    async def test_high_repetition_first_correction(self) -> None:
        fp = "search:same_hash_12345"
        turns = tuple(_turn(i + 1, (fp,)) for i in range(5))
        detector = ToolRepetitionDetector()
        result = await detector.check(turns, corrections_injected=0)
        assert result.verdict == StagnationVerdict.INJECT_PROMPT
        assert result.corrective_message is not None
        assert "search:same_hash_12345" in result.corrective_message
        assert result.repetition_ratio > 0.0

    async def test_cycle_detected_first_correction(self) -> None:
        """A->B->A->B pattern triggers detection."""
        turns = (
            _turn(1, ("tool_a:hash_a_123456789",)),
            _turn(2, ("tool_b:hash_b_123456789",)),
            _turn(3, ("tool_a:hash_a_123456789",)),
            _turn(4, ("tool_b:hash_b_123456789",)),
        )
        detector = ToolRepetitionDetector(
            StagnationConfig(min_tool_turns=2, repetition_threshold=1.0),
        )
        result = await detector.check(turns, corrections_injected=0)
        assert result.verdict == StagnationVerdict.INJECT_PROMPT
        assert result.cycle_length == 2


@pytest.mark.unit
class TestToolRepetitionDetectorTerminate:
    """Cases that should return TERMINATE."""

    async def test_after_max_corrections(self) -> None:
        fp = "search:same_hash_12345"
        turns = tuple(_turn(i + 1, (fp,)) for i in range(5))
        detector = ToolRepetitionDetector(
            StagnationConfig(max_corrections=1),
        )
        result = await detector.check(turns, corrections_injected=1)
        assert result.verdict == StagnationVerdict.TERMINATE
        assert result.corrective_message is None

    async def test_max_corrections_zero(self) -> None:
        """max_corrections=0 skips INJECT_PROMPT, goes to TERMINATE."""
        fp = "search:same_hash_12345"
        turns = tuple(_turn(i + 1, (fp,)) for i in range(5))
        detector = ToolRepetitionDetector(
            StagnationConfig(max_corrections=0),
        )
        result = await detector.check(turns, corrections_injected=0)
        assert result.verdict == StagnationVerdict.TERMINATE


@pytest.mark.unit
class TestToolRepetitionDetectorCycleControl:
    """Cycle detection enable/disable."""

    async def test_cycle_disabled_no_trigger(self) -> None:
        """A->B->A->B does NOT trigger when cycle_detection=False."""
        turns = (
            _turn(1, ("tool_a:hash_a_123456789",)),
            _turn(2, ("tool_b:hash_b_123456789",)),
            _turn(3, ("tool_a:hash_a_123456789",)),
            _turn(4, ("tool_b:hash_b_123456789",)),
        )
        # Repetition ratio: 2 duplicates out of 4 = 0.5, below 1.0
        detector = ToolRepetitionDetector(
            StagnationConfig(
                cycle_detection=False,
                repetition_threshold=1.0,
                min_tool_turns=2,
            ),
        )
        result = await detector.check(turns)
        assert result.verdict == StagnationVerdict.NO_STAGNATION


@pytest.mark.unit
class TestToolRepetitionDetectorWindow:
    """Window size and turn filtering."""

    async def test_window_respects_size(self) -> None:
        """Older turns outside window are excluded."""
        old_fp = "old_tool:old_hash_1234567"
        new_fp = "new_tool:new_hash_1234567"
        turns = (
            _turn(1, (old_fp,)),
            _turn(2, (old_fp,)),
            _turn(3, (old_fp,)),
            # Window of 2 should only include these:
            _turn(4, (new_fp,)),
            _turn(5, ("other:unique_hash_123456",)),
        )
        detector = ToolRepetitionDetector(
            StagnationConfig(
                window_size=2,
                min_tool_turns=2,
                cycle_detection=False,
            ),
        )
        result = await detector.check(turns)
        # In the window of 2, fingerprints are unique
        assert result.verdict == StagnationVerdict.NO_STAGNATION

    async def test_non_tool_turns_excluded(self) -> None:
        """Turns without fingerprints are excluded from the window."""
        fp = "search:same_hash_12345"
        turns = (
            _turn(1, (fp,)),
            _turn(2),  # no tools
            _turn(3),  # no tools
            _turn(4, (fp,)),
        )
        detector = ToolRepetitionDetector(
            StagnationConfig(
                min_tool_turns=2,
                repetition_threshold=0.4,  # 0.5 ratio > 0.4 threshold
            ),
        )
        result = await detector.check(turns)
        # Only 2 tool turns with identical fingerprints → ratio 0.5
        assert result.verdict != StagnationVerdict.NO_STAGNATION


@pytest.mark.unit
class TestToolRepetitionDetectorMisc:
    """Miscellaneous detector behavior."""

    def test_detector_type(self) -> None:
        detector = ToolRepetitionDetector()
        assert detector.get_detector_type() == "tool_repetition"

    def test_default_config(self) -> None:
        detector = ToolRepetitionDetector()
        assert detector.config == StagnationConfig()

    def test_custom_config(self) -> None:
        config = StagnationConfig(window_size=10)
        detector = ToolRepetitionDetector(config)
        assert detector.config.window_size == 10

    def test_protocol_conformance(self) -> None:
        from synthorg.engine.stagnation.protocol import StagnationDetector

        detector = ToolRepetitionDetector()
        assert isinstance(detector, StagnationDetector)


@pytest.mark.unit
class TestDetectCycle:
    """Direct tests for _detect_cycle helper."""

    def test_no_cycle_short_sequence(self) -> None:
        from synthorg.engine.stagnation.detector import _detect_cycle

        a: tuple[str, ...] = ("a:1",)
        b: tuple[str, ...] = ("b:2",)
        c: tuple[str, ...] = ("c:3",)
        assert _detect_cycle([a]) is None
        assert _detect_cycle([a, b]) is None
        assert _detect_cycle([a, b, c]) is None

    def test_cycle_length_2(self) -> None:
        from synthorg.engine.stagnation.detector import _detect_cycle

        a: tuple[str, ...] = ("a:1",)
        b: tuple[str, ...] = ("b:2",)
        assert _detect_cycle([a, b, a, b]) == 2

    def test_cycle_length_3(self) -> None:
        from synthorg.engine.stagnation.detector import _detect_cycle

        a: tuple[str, ...] = ("a:1",)
        b: tuple[str, ...] = ("b:2",)
        c: tuple[str, ...] = ("c:3",)
        assert _detect_cycle([a, b, c, a, b, c]) == 3

    def test_no_cycle_almost_match(self) -> None:
        from synthorg.engine.stagnation.detector import _detect_cycle

        a: tuple[str, ...] = ("a:1",)
        b: tuple[str, ...] = ("b:2",)
        c: tuple[str, ...] = ("c:3",)
        assert _detect_cycle([a, b, a, c]) is None

    def test_shortest_cycle_preferred(self) -> None:
        from synthorg.engine.stagnation.detector import _detect_cycle

        # A A A A -- cycle length 2 matches before 3
        a: tuple[str, ...] = ("a:1",)
        assert _detect_cycle([a, a, a, a]) == 2

    def test_empty_sequence(self) -> None:
        from synthorg.engine.stagnation.detector import _detect_cycle

        assert _detect_cycle([]) is None


@pytest.mark.unit
class TestRepetitionRatioExactValues:
    """Verify exact repetition ratio computation."""

    async def test_all_identical_ratio(self) -> None:
        """5 identical fingerprints: 4 duplicates / 5 total = 0.8."""
        fp = "search:same_hash_12345"
        turns = tuple(_turn(i + 1, (fp,)) for i in range(5))
        detector = ToolRepetitionDetector(
            StagnationConfig(min_tool_turns=2),
        )
        result = await detector.check(turns, corrections_injected=0)
        assert result.repetition_ratio == pytest.approx(0.8)

    async def test_two_distinct_repeated_ratio(self) -> None:
        """A,A,B,B: 2 duplicates / 4 total = 0.5."""
        turns = (
            _turn(1, ("a:hash_a_1234567890",)),
            _turn(2, ("a:hash_a_1234567890",)),
            _turn(3, ("b:hash_b_1234567890",)),
            _turn(4, ("b:hash_b_1234567890",)),
        )
        detector = ToolRepetitionDetector(
            StagnationConfig(
                min_tool_turns=2,
                repetition_threshold=1.0,
                cycle_detection=False,
            ),
        )
        result = await detector.check(turns)
        assert result.repetition_ratio == pytest.approx(0.5)

    async def test_single_occurrence_zero_ratio(self) -> None:
        """All unique fingerprints: 0 duplicates = 0.0."""
        turns = (
            _turn(1, ("a:hash_a_1234567890",)),
            _turn(2, ("b:hash_b_1234567890",)),
        )
        detector = ToolRepetitionDetector(
            StagnationConfig(min_tool_turns=2, cycle_detection=False),
        )
        result = await detector.check(turns)
        assert result.repetition_ratio == pytest.approx(0.0)
