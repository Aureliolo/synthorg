"""Tests for QualityErosionDetector."""

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.stagnation.models import (
    StagnationReason,
    StagnationVerdict,
)
from synthorg.engine.stagnation.quality_erosion_detector import (
    QualityErosionDetector,
)
from synthorg.execution.turn import TurnRecord


def _make_turn(
    turn_number: int,
    tool_calls: tuple[str, ...] = ("read",),
    fingerprints: tuple[str, ...] = ("read:abc",),
) -> TurnRecord:
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=100,
        output_tokens=50,
        cost=0.01,
        finish_reason=FinishReason.STOP,
        tool_calls_made=tool_calls,
        tool_call_fingerprints=fingerprints,
    )


@pytest.mark.unit
class TestQualityErosionDetectorInit:
    """QualityErosionDetector construction and properties."""

    def test_defaults(self) -> None:
        detector = QualityErosionDetector()
        assert detector.threshold == 0.5
        assert detector.window_size == 10
        assert detector.get_detector_type() == "quality_erosion"

    def test_custom_threshold(self) -> None:
        detector = QualityErosionDetector(threshold=0.3)
        assert detector.threshold == 0.3

    def test_invalid_threshold(self) -> None:
        with pytest.raises(ValueError, match="threshold"):
            QualityErosionDetector(threshold=1.5)

    def test_invalid_window_size_too_small(self) -> None:
        with pytest.raises(ValueError, match="window_size"):
            QualityErosionDetector(window_size=1)

    def test_invalid_window_size_too_large(self) -> None:
        with pytest.raises(ValueError, match="window_size"):
            QualityErosionDetector(window_size=51)


@pytest.mark.unit
class TestQualityErosionDetectorCheck:
    """QualityErosionDetector.check() behavior."""

    async def test_no_stagnation_below_threshold(self) -> None:
        detector = QualityErosionDetector(threshold=0.9)
        # Distinct fingerprints -> low erosion score.
        turns = tuple(
            _make_turn(i, (f"tool{i}",), (f"tool{i}:hash{i}",)) for i in range(1, 6)
        )
        result = await detector.check(turns)
        assert result.verdict == StagnationVerdict.NO_STAGNATION
        assert result.reason is None

    async def test_stagnation_above_threshold(self) -> None:
        detector = QualityErosionDetector(threshold=0.1)
        # All identical fingerprints -> high erosion.
        turns = tuple(_make_turn(i, ("read",), ("read:abc",)) for i in range(1, 12))
        result = await detector.check(turns)
        assert result.verdict in (
            StagnationVerdict.INJECT_PROMPT,
            StagnationVerdict.TERMINATE,
        )
        assert result.reason == StagnationReason.QUALITY_EROSION

    async def test_inject_prompt_first(self) -> None:
        detector = QualityErosionDetector(threshold=0.1)
        turns = tuple(_make_turn(i, ("read",), ("read:abc",)) for i in range(1, 12))
        result = await detector.check(turns, corrections_injected=0)
        assert result.verdict == StagnationVerdict.INJECT_PROMPT
        assert result.corrective_message is not None
        assert "erosion" in result.corrective_message.lower()

    async def test_terminate_after_correction(self) -> None:
        detector = QualityErosionDetector(threshold=0.1)
        turns = tuple(_make_turn(i, ("read",), ("read:abc",)) for i in range(1, 12))
        result = await detector.check(turns, corrections_injected=1)
        assert result.verdict == StagnationVerdict.TERMINATE
        assert result.corrective_message is None

    async def test_empty_turns(self) -> None:
        detector = QualityErosionDetector()
        result = await detector.check(())
        assert result.verdict == StagnationVerdict.NO_STAGNATION
