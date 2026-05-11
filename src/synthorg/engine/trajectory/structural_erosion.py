"""Structural erosion metrics for quality degradation detection.

Three sub-metrics composited into a single score (0.0-1.0):
- **Duplicated-block ratio**: fraction of turns whose fingerprints
  duplicate a prior turn's fingerprints.
- **Cyclomatic complexity delta**: growth in distinct tool-call
  patterns between the first and second half of the window.
- **Dead-branch ratio**: fraction of tool calls whose results
  were never consumed by a subsequent tool call.

Source: SlopCodeBench (arXiv:2603.24755) -- adapted from code
quality metrics to tool-integrated reasoning traces.
"""

from typing import Final

from synthorg.engine.loop_protocol import TurnRecord  # noqa: TC001
from synthorg.observability import get_logger

logger = get_logger(__name__)

# Composite weights (sum to 1.0).
_WEIGHT_DUPLICATED: Final[float] = 0.4
_WEIGHT_CYCLOMATIC: Final[float] = 0.4
_WEIGHT_DEAD_BRANCH: Final[float] = 0.2

# Minimum turn thresholds for each sub-metric.
_MIN_TURNS_DUPLICATED: Final[int] = 2
_MIN_TURNS_CYCLOMATIC: Final[int] = 4
_MIN_TURNS_DEAD_BRANCH: Final[int] = 3

# Default sliding-window size for the four detect_* helpers.
_DEFAULT_WINDOW_SIZE: Final[int] = 10


def detect_duplicated_blocks(
    turns: tuple[TurnRecord, ...],
    *,
    window_size: int = _DEFAULT_WINDOW_SIZE,
) -> float:
    """Fraction of turns whose fingerprints duplicate an earlier turn.

    Compares each turn's ``tool_call_fingerprints`` tuple against
    all prior turns in the window.  Exact-match only (no fuzzy).

    Args:
        turns: Ordered turn records.
        window_size: Maximum turns to analyze.

    Returns:
        Ratio in [0.0, 1.0].  0.0 if fewer than 2 turns.
    """
    window = _slice_tool_turns(turns, window_size)
    if len(window) < _MIN_TURNS_DUPLICATED:
        return 0.0

    seen: set[tuple[str, ...]] = set()
    duplicates = 0
    for turn in window:
        fps = turn.tool_call_fingerprints
        if not fps:
            continue
        if fps in seen:
            duplicates += 1
        seen.add(fps)

    total = len(window)
    return duplicates / total if total > 0 else 0.0


def compute_cyclomatic_complexity_delta(
    turns: tuple[TurnRecord, ...],
    *,
    window_size: int = _DEFAULT_WINDOW_SIZE,
) -> float:
    """Growth in distinct tool-call patterns between window halves.

    Splits the window into first-half and second-half, counts
    distinct fingerprints in each, and returns the normalized
    increase.  Only positive deltas (increasing complexity) count;
    simplification is clamped to 0.0.

    Args:
        turns: Ordered turn records.
        window_size: Maximum turns to analyze.

    Returns:
        Normalized delta in [0.0, 1.0].  0.0 if insufficient data.
    """
    window = _slice_tool_turns(turns, window_size)
    if len(window) < _MIN_TURNS_CYCLOMATIC:
        return 0.0

    mid = len(window) // 2
    first_half = window[:mid]
    second_half = window[mid:]

    unique_first = _count_unique_fingerprints(first_half)
    unique_second = _count_unique_fingerprints(second_half)

    if unique_first == 0:
        return 0.0

    delta = (unique_second - unique_first) / unique_first
    # Only positive delta (increasing complexity) counts.
    return min(max(delta, 0.0), 1.0)


def detect_dead_branches(
    turns: tuple[TurnRecord, ...],
    *,
    window_size: int = _DEFAULT_WINDOW_SIZE,
) -> float:
    """Fraction of tool calls whose results were never consumed.

    Heuristic: a tool call is a "dead branch" if the tool name
    does not appear in any subsequent turn's tool_calls_made (i.e.
    no follow-up tool referenced the same tool name).  This is a
    coarse proxy for "unused output" in non-code traces.

    Args:
        turns: Ordered turn records.
        window_size: Maximum turns to analyze.

    Returns:
        Ratio in [0.0, 1.0].  0.0 if fewer than 3 turns.
    """
    window = _slice_tool_turns(turns, window_size)
    if len(window) < _MIN_TURNS_DEAD_BRANCH:
        return 0.0

    total_calls = 0
    dead_calls = 0

    for i, turn in enumerate(window[:-1]):
        for tool_name in turn.tool_calls_made:
            total_calls += 1
            # Check if any subsequent turn references the same tool.
            # tool_calls_made contains plain tool names (not fingerprints).
            consumed = any(
                tool_name in later.tool_calls_made for later in window[i + 1 :]
            )
            if not consumed:
                dead_calls += 1

    return dead_calls / total_calls if total_calls > 0 else 0.0


def compute_structural_erosion_score(
    turns: tuple[TurnRecord, ...],
    *,
    window_size: int = _DEFAULT_WINDOW_SIZE,
) -> float:
    """Composite structural erosion score.

    Weighted combination of three sub-metrics:
    ``0.4 * duplicated + 0.4 * cyclomatic + 0.2 * dead_branch``

    Args:
        turns: Ordered turn records.
        window_size: Maximum turns to analyze.

    Returns:
        Score in [0.0, 1.0] where 0.0 = no erosion, 1.0 = severe.
    """
    dup = detect_duplicated_blocks(turns, window_size=window_size)
    cyc = compute_cyclomatic_complexity_delta(turns, window_size=window_size)
    dead = detect_dead_branches(turns, window_size=window_size)

    score = (
        _WEIGHT_DUPLICATED * dup + _WEIGHT_CYCLOMATIC * cyc + _WEIGHT_DEAD_BRANCH * dead
    )
    return min(max(score, 0.0), 1.0)


# ── Helpers ────────────────────────────────────────────────────────


def _slice_tool_turns(
    turns: tuple[TurnRecord, ...],
    window_size: int,
) -> tuple[TurnRecord, ...]:
    """Extract the most recent tool-bearing turns within window."""
    tool_turns = tuple(t for t in turns if t.tool_calls_made)
    return tuple(tool_turns[-window_size:])


def _count_unique_fingerprints(
    turns: tuple[TurnRecord, ...],
) -> int:
    """Count distinct fingerprints across turns."""
    seen: set[str] = set()
    for turn in turns:
        for fp in turn.tool_call_fingerprints:
            seen.add(fp)
    return len(seen)
