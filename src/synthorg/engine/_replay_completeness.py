"""How much of a run a replay actually recovered.

A replay reads a session back from its event stream, and the stream can be
short: events are dropped, retransmitted, or never written. The score says
how much of the run the caller is looking at, so a partial replay is not
mistaken for the whole thing.

Separate from the session module because the weights are a tunable set that
has to be read together to be judged, and because the scoring is a pure
function of what was found: nothing here touches a session, a store or a
clock.
"""

from typing import Final

#: Replay completeness at or above which the replay is considered full.
COMPLETENESS_THRESHOLD: Final[float] = 0.85

# What each recovered signal is worth. Named rather than inlined because they
# are the same class of tunable as every scoring weight in
# ``settings/definitions/``: a reader changing one needs to see the others it
# is balanced against.
_WEIGHT_ENGINE_START: Final[float] = 0.15
_WEIGHT_CONTEXT_CREATED: Final[float] = 0.10
_WEIGHT_ANY_TURN: Final[float] = 0.20
#: A bonus on top of :data:`_WEIGHT_ANY_TURN`, not an alternative to it: turns
#: numbered 1..n with no gaps mean nothing was dropped between them.
_WEIGHT_CONTIGUOUS_TURNS: Final[float] = 0.25
_WEIGHT_COST_PRESENT: Final[float] = 0.15
_WEIGHT_TRANSITION: Final[float] = 0.15

#: The weights sum to exactly 1.0, so a replay that recovered everything
#: scores 1.0 and nothing can exceed it. The clamp is therefore a guard on the
#: weights rather than on the arithmetic: it is what makes editing one of them
#: a scoring change instead of a silent contract break.
#:
#: Tolerance for a partial replay comes from :data:`COMPLETENESS_THRESHOLD`,
#: not from slack in this total. At 0.85 a replay may miss any single signal
#: worth 0.15 or less and still read as complete.
_MAX_COMPLETENESS: Final[float] = 1.0


def compute_completeness(
    *,
    found_engine_start: bool,
    found_context_created: bool,
    turn_numbers: list[int],
    total_cost: float,
    found_transition: bool,
) -> float:
    """Compute replay completeness as a weighted additive score.

    Each condition contributes independently, and the total is clamped. The
    weights themselves are the module constants above rather than a table
    repeated here, which would be a second copy free to drift from the
    arithmetic.

    Args:
        found_engine_start: Whether the engine-start event was recovered.
        found_context_created: Whether the context-created event was.
        turn_numbers: Every turn number seen, duplicates included.
        total_cost: Cost summed across the recovered turn events.
        found_transition: Whether any task-transition event was recovered.

    Returns:
        The clamped completeness score in ``[0.0, 1.0]``.
    """
    score = 0.0

    if found_engine_start:
        score += _WEIGHT_ENGINE_START
    if found_context_created:
        score += _WEIGHT_CONTEXT_CREATED
    if turn_numbers:
        score += _WEIGHT_ANY_TURN
        # Deduplicate before the contiguity check so a retransmitted turn
        # event does not read as a gap and penalise the score.
        unique_turns = sorted(set(turn_numbers))
        expected = list(range(1, len(unique_turns) + 1))
        if unique_turns == expected:
            score += _WEIGHT_CONTIGUOUS_TURNS
    if total_cost > 0.0:
        score += _WEIGHT_COST_PRESENT
    if found_transition:
        score += _WEIGHT_TRANSITION

    return min(score, _MAX_COMPLETENESS)


__all__ = ["COMPLETENESS_THRESHOLD", "compute_completeness"]
