# module-kind: code
"""Shared conversation-transcript rendering.

A single place that flattens ordered :class:`ConversationTurn` rows into
a prompt-ready transcript, used by the clarify-and-propose loop, the
concern-routing classifier, and the multi-agent group chat so every
surface renders history identically before fencing it as untrusted
content.
"""

from collections.abc import Callable

from synthorg.engine.token_estimation import PromptTokenEstimator
from synthorg.meta.chief_of_staff.models import ConversationTurn


def _render_turn(turn: ConversationTurn) -> str:
    """Render a single turn as one ``ROLE: content`` transcript line.

    Returns:
        The transcript line for *turn*.
    """
    return f"{turn.role.value.upper()}: {turn.content}"


def render_turns_transcript(turns: tuple[ConversationTurn, ...]) -> str:
    """Render chronological turns into a prompt-ready transcript.

    Each turn becomes one ``ROLE: content`` line in sequence order. The
    result is the raw transcript body; callers wrap it via
    :func:`wrap_untrusted` before injecting it into a prompt.

    Args:
        turns: Conversation turns, oldest first.

    Returns:
        The newline-joined transcript.
    """
    return "\n".join(_render_turn(turn) for turn in turns)


def window_turns(
    turns: tuple[ConversationTurn, ...],
    *,
    token_budget: int,
    estimator: PromptTokenEstimator,
    render_turn: Callable[[ConversationTurn], str] = _render_turn,
) -> tuple[ConversationTurn, ...]:
    """Select the most recent turns that fit within a token budget.

    Walks the thread newest-first, keeping turns while their cumulative
    estimated token count stays within *token_budget*, then returns the
    kept turns in chronological order. The single newest turn is always
    kept even if it alone exceeds the budget, so the model never loses the
    pending message; only older context is dropped. This bounds prompt
    growth on a long-running conversation without truncating mid-turn.

    Args:
        turns: Conversation turns, oldest first.
        token_budget: Maximum estimated tokens the kept window may use.
        estimator: Token estimator used to size each rendered turn.
        render_turn: Renders one turn to the string whose tokens are
            counted; pass the same renderer the caller will inject so the
            estimate matches the eventual prompt (defaults to the
            ``ROLE: content`` form).

    Returns:
        The kept turns, oldest first.
    """
    if not turns:
        return ()
    kept_reversed: list[ConversationTurn] = []
    used = 0
    for turn in reversed(turns):
        cost = estimator.estimate_tokens(render_turn(turn))
        if kept_reversed and used + cost > token_budget:
            break
        kept_reversed.append(turn)
        used += cost
    kept_reversed.reverse()
    return tuple(kept_reversed)


def windowed_transcript(
    turns: tuple[ConversationTurn, ...],
    *,
    token_budget: int,
    estimator: PromptTokenEstimator,
) -> str:
    """Render the most recent turns that fit within a token budget.

    Convenience wrapper over :func:`window_turns` for the ``ROLE: content``
    transcript form used by the clarify-and-propose loop.

    Args:
        turns: Conversation turns, oldest first.
        token_budget: Maximum estimated tokens the rendered window may use.
        estimator: Token estimator used to size each rendered turn.

    Returns:
        The newline-joined transcript of the kept window.
    """
    return render_turns_transcript(
        window_turns(turns, token_budget=token_budget, estimator=estimator)
    )


__all__ = ["render_turns_transcript", "window_turns", "windowed_transcript"]
