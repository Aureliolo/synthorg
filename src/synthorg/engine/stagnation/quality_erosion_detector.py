"""Quality erosion stagnation detector.

Detects progressive quality degradation in agent output by
monitoring structural erosion metrics across a sliding window.
Implements the ``StagnationDetector`` protocol.

Source: SlopCodeBench (arXiv:2603.24755) -- agents keep working
but quality erodes (rising verbosity, duplicate blocks, increasing
complexity).
"""

from typing import Final

from synthorg.engine.trajectory.structural_erosion import (
    compute_structural_erosion_score,
)
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger
from synthorg.observability.events.stagnation import (
    QUALITY_STAGNATION_DETECTED,
    STAGNATION_CHECK_PERFORMED,
    STAGNATION_QUALITY_EROSION_CONFIG_ERROR,
)

from .models import (
    NO_STAGNATION_RESULT,
    StagnationReason,
    StagnationResult,
    StagnationVerdict,
)

logger = get_logger(__name__)
_DEFAULT_THRESHOLD: Final[float] = 0.5
_DEFAULT_WINDOW_SIZE: Final[int] = 10

_MIN_WINDOW_SIZE: int = 2
_MAX_WINDOW_SIZE: int = 50


class QualityErosionDetector:
    """Detects stagnation via progressive structural quality erosion.

    Computes the structural erosion score across a sliding window
    and triggers when it exceeds the configured threshold.

    Args:
        threshold: Erosion score that triggers detection (0.0-1.0).
        window_size: Number of recent tool-bearing turns to analyze.
    """

    def __init__(
        self,
        *,
        threshold: float = _DEFAULT_THRESHOLD,
        window_size: int = _DEFAULT_WINDOW_SIZE,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            msg = f"threshold must be in [0.0, 1.0], got {threshold}"
            logger.warning(
                STAGNATION_QUALITY_EROSION_CONFIG_ERROR,
                field="threshold",
                value=threshold,
                bounds=(0.0, 1.0),
            )
            raise ValueError(msg)
        if not _MIN_WINDOW_SIZE <= window_size <= _MAX_WINDOW_SIZE:
            msg = (
                f"window_size must be in "
                f"[{_MIN_WINDOW_SIZE}, {_MAX_WINDOW_SIZE}], "
                f"got {window_size}"
            )
            logger.warning(
                STAGNATION_QUALITY_EROSION_CONFIG_ERROR,
                field="window_size",
                value=window_size,
                bounds=(_MIN_WINDOW_SIZE, _MAX_WINDOW_SIZE),
            )
            raise ValueError(msg)
        self._threshold = threshold
        self._window_size = window_size

    @property
    def threshold(self) -> float:
        """Return the erosion threshold."""
        return self._threshold

    @property
    def window_size(self) -> int:
        """Return the analysis window size."""
        return self._window_size

    def get_detector_type(self) -> str:
        """Return the detector type identifier."""
        return "quality_erosion"

    async def check(
        self,
        turns: tuple[TurnRecord, ...],
        *,
        corrections_injected: int = 0,
    ) -> StagnationResult:
        """Check for quality erosion in recent turns.

        Args:
            turns: Ordered turn records from the current scope.
            corrections_injected: Corrective prompts already injected.

        Returns:
            A ``StagnationResult`` with the verdict and data.
        """
        score = compute_structural_erosion_score(
            turns,
            window_size=self._window_size,
        )

        if score < self._threshold:
            logger.debug(
                STAGNATION_CHECK_PERFORMED,
                verdict="no_stagnation",
                detector="quality_erosion",
                erosion_score=score,
                threshold=self._threshold,
            )
            return NO_STAGNATION_RESULT

        logger.info(
            QUALITY_STAGNATION_DETECTED,
            erosion_score=score,
            threshold=self._threshold,
            corrections_injected=corrections_injected,
            window_size=self._window_size,
        )

        if corrections_injected < 1:
            return StagnationResult(
                verdict=StagnationVerdict.INJECT_PROMPT,
                reason=StagnationReason.QUALITY_EROSION,
                corrective_message=_build_corrective_message(score),
                details={"erosion_score": score},
            )

        return StagnationResult(
            verdict=StagnationVerdict.TERMINATE,
            reason=StagnationReason.QUALITY_EROSION,
            details={"erosion_score": score},
        )


def _build_corrective_message(erosion_score: float) -> str:
    """Build a corrective message for quality erosion.

    Returns:
        A bracketed system-intervention prompt embedding the erosion
        score and suggesting simplification.
    """
    return (
        "[SYSTEM INTERVENTION: Quality erosion detected -- your recent "
        "output shows rising structural degradation (duplicated blocks, "
        "increasing complexity, unused tool calls). Erosion score: "
        f"{erosion_score:.2f}. Simplify your approach: reduce "
        "redundant tool calls, avoid repeating prior outputs, and "
        "focus on the most direct path to completion.]"
    )
