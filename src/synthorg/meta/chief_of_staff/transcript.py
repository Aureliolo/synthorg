# module-kind: code
"""Shared conversation-transcript rendering.

A single place that flattens ordered :class:`ConversationTurn` rows into
a prompt-ready transcript, used by the clarify-and-propose loop, the
concern-routing classifier, and the multi-agent group chat so every
surface renders history identically before fencing it as untrusted
content.
"""

from synthorg.meta.chief_of_staff.models import ConversationTurn


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
    return "\n".join(f"{turn.role.value.upper()}: {turn.content}" for turn in turns)


__all__ = ["render_turns_transcript"]
