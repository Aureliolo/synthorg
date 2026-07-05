# module-kind: code
"""Per-round token budgeting for the multi-agent group chat.

Pure budget arithmetic split from ``group_chat.py`` so the round-loop
service stays within its size tier: the round-stop bound and the per-turn
output cap that reserves room for the estimated input prompt before
dispatch.
"""

from synthorg.communication.meeting._token_tracker import TokenTracker
from synthorg.engine.token_estimation import PromptTokenEstimator
from synthorg.meta.chief_of_staff.enums import GroupChatTruncationReason
from synthorg.meta.chief_of_staff.group_models import AttributedContribution
from synthorg.meta.chief_of_staff.group_prompt import estimate_peer_tokens


def round_bound(
    tracker: TokenTracker,
    reserve: int,
    total_turns: int,
    *,
    max_total_turns: int,
) -> GroupChatTruncationReason | None:
    """Return the bound that stops the round now, or ``None`` to continue.

    Returns:
        The tripped truncation reason, or ``None`` when the round may run
        the next participant.
    """
    if total_turns >= max_total_turns:
        return GroupChatTruncationReason.MAX_TOTAL_TURNS_REACHED
    if tracker.remaining <= reserve:
        return GroupChatTruncationReason.TOKEN_BUDGET_EXHAUSTED
    return None


def bounded_call_max_tokens(  # noqa: PLR0913 -- budget inputs, all independent
    history_tokens: int,
    prior_contributions: list[AttributedContribution],
    *,
    estimator: PromptTokenEstimator,
    remaining: int,
    reserve: int,
    per_agent_max: int,
) -> int:
    """Cap the next contribution's output so input + output fit the budget.

    Reserves room for the estimated INPUT prompt before dispatch, so a
    large history cannot consume the whole remaining round budget on one
    call. A non-positive result means the input alone leaves no room for
    the output reserve and the round should stop.

    *history_tokens* is the round-frozen history estimate the caller
    computed once; only the growing peer block is re-estimated here.

    Returns:
        The output-token cap for the next call; ``<= 0`` when the round
        should stop instead of dispatching.
    """
    estimated_input = history_tokens + estimate_peer_tokens(
        prior_contributions, estimator=estimator
    )
    return min(per_agent_max, remaining - reserve - estimated_input)


__all__ = ["bounded_call_max_tokens", "round_bound"]
