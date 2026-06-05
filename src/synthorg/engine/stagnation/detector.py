"""Default stagnation detector -- tool repetition analysis.

Implements the ``StagnationDetector`` protocol using dual-signal
detection: repetition ratio and cycle detection across a sliding
window of recent tool-bearing turns.
"""

from collections import Counter

from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger
from synthorg.observability.events.stagnation import (
    STAGNATION_CHECK_PERFORMED,
    STAGNATION_DETECTED,
)

from .models import (
    NO_STAGNATION_RESULT,
    StagnationConfig,
    StagnationReason,
    StagnationResult,
    StagnationVerdict,
)

logger = get_logger(__name__)


class ToolRepetitionDetector:
    """Detects stagnation via repeated tool-call fingerprints.

    Uses two signals:

    1. **Repetition ratio** -- fraction of fingerprints in the window
       that appear more than once.
    2. **Cycle detection** -- checks for repeating A->B->A->B patterns
       at the turn level.

    Args:
        config: Detection configuration.  Defaults to
            ``StagnationConfig()``.
    """

    def __init__(self, config: StagnationConfig | None = None) -> None:
        self._config = config or StagnationConfig()

    @property
    def config(self) -> StagnationConfig:
        """Return the detector configuration."""
        return self._config

    def get_detector_type(self) -> str:
        """Return the detector type identifier."""
        return "tool_repetition"

    async def check(
        self,
        turns: tuple[TurnRecord, ...],
        *,
        corrections_injected: int = 0,
    ) -> StagnationResult:
        """Check for stagnation in recent turns.

        Args:
            turns: Ordered turn records from the current scope.
            corrections_injected: Corrective prompts already injected.

        Returns:
            A ``StagnationResult`` with the verdict and data.
        """
        if not self._config.enabled:
            return NO_STAGNATION_RESULT

        window = self._extract_window(turns)
        if window is None:
            return NO_STAGNATION_RESULT

        ratio, counts = _compute_repetition_ratio(window)
        cycle_length = _detect_cycle_if_enabled(
            window, enabled=self._config.cycle_detection
        )
        stagnation_detected = (
            ratio >= self._config.repetition_threshold or cycle_length is not None
        )

        if not stagnation_detected:
            logger.debug(
                STAGNATION_CHECK_PERFORMED,
                verdict="no_stagnation",
                repetition_ratio=ratio,
                window_size=len(window),
            )
            return StagnationResult(
                verdict=StagnationVerdict.NO_STAGNATION,
                repetition_ratio=ratio,
                cycle_length=None,
            )

        return _build_stagnation_result(
            ratio,
            cycle_length,
            counts,
            corrections_injected,
            self._config.max_corrections,
        )

    def _extract_window(
        self,
        turns: tuple[TurnRecord, ...],
    ) -> tuple[TurnRecord, ...] | None:
        """Filter and slice turns to the analysis window.

        Returns:
            The trailing window of tool-bearing turns; ``None`` when
            fewer than ``min_tool_turns`` tool turns exist.
        """
        tool_turns = tuple(t for t in turns if t.tool_call_fingerprints)
        if len(tool_turns) < self._config.min_tool_turns:
            return None
        return tuple(tool_turns[-self._config.window_size :])


def _compute_repetition_ratio(
    window: tuple[TurnRecord, ...],
) -> tuple[float, Counter[str]]:
    """Compute the repetition ratio for fingerprints in the window.

    Returns:
        ``(ratio, counts)`` where *ratio* is the number of excess
        (above-first) occurrences of repeated fingerprints divided by
        the total fingerprint count, and *counts* is the ``Counter``
        over all fingerprints.
    """
    all_fingerprints: list[str] = []
    for t in window:
        all_fingerprints.extend(t.tool_call_fingerprints)

    if not all_fingerprints:
        return 0.0, Counter()

    counts: Counter[str] = Counter(all_fingerprints)
    duplicate_count = sum(c - 1 for c in counts.values() if c > 1)
    total = len(all_fingerprints)
    return duplicate_count / total, counts


def _detect_cycle_if_enabled(
    window: tuple[TurnRecord, ...],
    *,
    enabled: bool,
) -> int | None:
    """Run cycle detection if enabled.

    Returns:
        The shortest detected cycle length when enabled and a cycle
        exists; ``None`` when disabled or no cycle is found.
    """
    if not enabled:
        return None
    turn_fps = [t.tool_call_fingerprints for t in window]
    return _detect_cycle(turn_fps)


def _detect_cycle(
    turn_fingerprints: list[tuple[str, ...]],
) -> int | None:
    """Detect repeating cycle patterns in turn fingerprints.

    Checks for cycle lengths from 2 up to len/2 where the tail
    of the sequence repeats: ``seq[-2k:-k] == seq[-k:]``.

    Args:
        turn_fingerprints: Per-turn fingerprint tuples.

    Returns:
        The shortest detected cycle length, or ``None``.
    """
    n = len(turn_fingerprints)
    for cycle_len in range(2, n // 2 + 1):
        tail = turn_fingerprints[-cycle_len:]
        preceding = turn_fingerprints[-2 * cycle_len : -cycle_len]
        if tail == preceding:
            return cycle_len
    return None


def _build_stagnation_result(
    repetition_ratio: float,
    cycle_length: int | None,
    counts: Counter[str],
    corrections_injected: int,
    max_corrections: int,
) -> StagnationResult:
    """Build INJECT_PROMPT or TERMINATE result after stagnation detected.

    Returns:
        A :class:`StagnationResult` with action INJECT_PROMPT when
        more corrections are available; TERMINATE when the
        ``max_corrections`` budget is exhausted.
    """
    repeated_tools = sorted({fp for fp, c in counts.items() if c > 1})
    reason = (
        StagnationReason.CYCLE_DETECTION
        if cycle_length is not None
        else StagnationReason.TOOL_REPETITION
    )

    logger.info(
        STAGNATION_DETECTED,
        repetition_ratio=repetition_ratio,
        cycle_length=cycle_length,
        repeated_tools=repeated_tools,
        corrections_injected=corrections_injected,
        max_corrections=max_corrections,
        reason=reason,
    )

    if corrections_injected < max_corrections:
        return StagnationResult(
            verdict=StagnationVerdict.INJECT_PROMPT,
            reason=reason,
            corrective_message=_build_corrective_message(
                repeated_tools,
            ),
            repetition_ratio=repetition_ratio,
            cycle_length=cycle_length,
            details={"repeated_tools": repeated_tools},
        )

    return StagnationResult(
        verdict=StagnationVerdict.TERMINATE,
        reason=reason,
        repetition_ratio=repetition_ratio,
        cycle_length=cycle_length,
        details={"repeated_tools": repeated_tools},
    )


def _build_corrective_message(repeated_tools: list[str]) -> str:
    """Build a corrective user-role message for prompt injection.

    Args:
        repeated_tools: Sorted list of repeated tool fingerprints.
            Always non-empty -- stagnation detection requires at least
            one repeated fingerprint (ratio or cycle signal).

    Returns:
        A corrective message string.
    """
    tool_list = ", ".join(repeated_tools)
    return (
        "[SYSTEM INTERVENTION: Stagnation detected -- your recent tool "
        "calls show a repeating pattern without progress. The following "
        "tools have been called with identical arguments multiple "
        f"times: {tool_list}. Try a different approach: modify your "
        "arguments, use different tools, or reconsider your strategy.]"
    )
