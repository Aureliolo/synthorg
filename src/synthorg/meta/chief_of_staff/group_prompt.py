# module-kind: code
"""Prompt + transcript rendering for the multi-agent group chat.

Extracted from ``group_chat.py`` so the round-loop service stays within
its size tier as the invite feature grows it. Pure rendering +
Untrusted-content fencing: the shared transcript and this round's peer contributions
are fenced (``<task-data>`` / ``<peer-contribution>``) before injection,
and the peer block is scanned for authority cues (detect-and-log, the
:class:`AuthorityDeferenceGuard` contract).

The authority scan is deliberately detect-and-log only; the
``<peer-contribution>`` fence is the actual injection defence (the model
treats fenced content as inert data regardless of its wording).
Redaction of matched cues is intentionally out of scope: an authority
phrase is often legitimate business content (a manager writing "I need
this by Friday"), so stripping it would cost signal for no security gain
over the fence. This is the terminal design, not a stopgap.
"""

from synthorg.communication.conversation.enums import ConversationRole
from synthorg.core.types import NotBlankStr
from synthorg.engine.middleware.s1_constraints import AuthorityDeferenceGuard
from synthorg.engine.prompt_safety import (
    TAG_PEER_CONTRIBUTION,
    TAG_TASK_DATA,
    wrap_untrusted,
)
from synthorg.engine.token_estimation import PromptTokenEstimator
from synthorg.meta.chief_of_staff.group_models import (
    AttributedContribution,
    ConversationParticipant,
)
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.observability import get_logger
from synthorg.observability.events.chief_of_staff import (
    COS_GROUP_AUTHORITY_CUES_DETECTED,
)

logger = get_logger(__name__)


def render_group_turn(turn: ConversationTurn) -> str:
    """Render one group turn as an attributed ``Speaker: content`` line.

    The human speaks as ``Human``; an agent turn is attributed to its
    stored name; anything else renders as ``Assistant``.

    Returns:
        The attributed transcript line for *turn*.
    """
    if turn.role is ConversationRole.USER:
        speaker = "Human"
    elif turn.role is ConversationRole.AGENT:
        speaker = turn.author_name or "Agent"
    else:
        speaker = "Assistant"
    return f"{speaker}: {turn.content}"


def render_group_history(turns: tuple[ConversationTurn, ...]) -> str:
    """Render attributed transcript lines for the group history.

    Returns:
        One ``Speaker: content`` line per turn (the human as ``Human``,
        each agent by its attributed name).
    """
    return "\n".join(render_group_turn(turn) for turn in turns)


def estimate_history_tokens(
    history: tuple[ConversationTurn, ...],
    *,
    estimator: PromptTokenEstimator,
) -> int:
    """Estimate the tokens the rendered history block will use.

    The history is frozen for the round, so a caller running many turns
    can size it once and reuse the result rather than re-rendering it per
    turn.

    Returns:
        The estimated token count of the rendered history block.
    """
    return estimator.estimate_tokens(render_group_history(history))


def estimate_peer_tokens(
    prior_contributions: list[AttributedContribution],
    *,
    estimator: PromptTokenEstimator,
) -> int:
    """Estimate the tokens this round's peer-contribution block will use.

    Returns:
        The estimated token count of the rendered peer block, the only
        part that grows as participants contribute within a round.
    """
    return estimator.estimate_tokens(render_round_contributions(prior_contributions))


def render_round_contributions(contributions: list[AttributedContribution]) -> str:
    """Render this round's peer contributions for the fenced block.

    Returns:
        One attributed line per contribution, or a placeholder when no
        peer has spoken yet this round.
    """
    if not contributions:
        return "(no contributions yet this round)"
    return "\n".join(
        f"{c.agent_name} ({c.participant_role}): {c.content}" for c in contributions
    )


def build_group_prompt(
    history: tuple[ConversationTurn, ...],
    prior_contributions: list[AttributedContribution],
    *,
    template: str,
    preamble: str | None = None,
) -> str:
    """Assemble the fenced contribution prompt for one participant.

    Args:
        history: The conversation history including this round's human
            message.
        prior_contributions: This round's earlier peer contributions.
        template: The contribution prompt template (plain or invite
            variant), selected by the caller.
        preamble: A pre-fenced handover block prepended above the
            prompt on an invited agent's first turn; ``None``
            for every other participant and the feature-off path.

    Returns:
        The formatted prompt: history + human message fenced as
        ``<task-data>``, this round's peer contributions fenced as
        ``<peer-contribution>``, optionally prefixed by *preamble*.
    """
    history_block = wrap_untrusted(TAG_TASK_DATA, render_group_history(history))
    peer_block = wrap_untrusted(
        TAG_PEER_CONTRIBUTION, render_round_contributions(prior_contributions)
    )
    body = template.format(
        conversation_history=history_block,
        prior_contributions=peer_block,
    )
    if preamble:
        return f"{preamble}\n\n{body}"
    return body


def audit_authority(
    guard: AuthorityDeferenceGuard,
    conversation_id: NotBlankStr,
    participant: ConversationParticipant,
    prior_contributions: list[AttributedContribution],
) -> None:
    """Scan the peer-contribution block for authority cues (audit only).

    Reuses the :class:`AuthorityDeferenceGuard` pattern scan so the same
    cues the agent-middleware path logs are recorded here, where a later
    participant could otherwise defer to an earlier peer's claimed
    authority. Detection + logging only (no redaction); the
    ``<peer-contribution>`` fencing is the injection defence.
    """
    if not prior_contributions:
        return
    peer_text = render_round_contributions(prior_contributions)
    cue_count = guard.scan(peer_text)
    if cue_count > 0:
        logger.info(
            COS_GROUP_AUTHORITY_CUES_DETECTED,
            conversation_id=conversation_id,
            recipient_agent_id=participant.agent_id,
            cue_count=cue_count,
        )


__all__ = [
    "audit_authority",
    "build_group_prompt",
    "estimate_history_tokens",
    "estimate_peer_tokens",
    "render_group_history",
    "render_group_turn",
    "render_round_contributions",
]
