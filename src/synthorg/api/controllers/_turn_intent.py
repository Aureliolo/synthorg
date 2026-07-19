# module-kind: service
"""Intent resolution for the unified conversational turn.

Resolves what an operator's message wants before dispatch: an explicit override,
a fixed-kind short-circuit for an in-flight GROUP thread, the wired classifier,
or the EXPLAIN default. Shared by the buffered dispatch and the streaming
endpoint so both classify identically; kept out of ``_turn_dispatch`` so that
module stays within its size budget.
"""

from synthorg.api.state import AppState
from synthorg.communication.conversation.enums import ConversationRole
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.enums import ConversationKind
from synthorg.meta.chief_of_staff.intent_router import (
    IntentOutcome,
    IntentRoutingReason,
    TurnIntent,
)
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.meta.state import MetaStateSlice


def _classification_history(
    body: str, app_state: AppState
) -> tuple[ConversationTurn, ...]:
    """Build a single-turn history for the intent classifier.

    Intent is dominated by the latest message, so classification runs on the
    message alone rather than loading the whole thread; the placeholder
    conversation id is never persisted (the turn only feeds the classifier's
    transcript renderer).

    Returns:
        A one-element history carrying the operator's message as a USER turn.
    """
    return (
        ConversationTurn(
            conversation_id=NotBlankStr("pending"),
            sequence=0,
            role=ConversationRole.USER,
            content=NotBlankStr(body),
            created_at=app_state.clock.now(),
        ),
    )


async def _fixed_kind_intent(
    app_state: AppState, conversation_id: NotBlankStr | None
) -> IntentOutcome | None:
    """Short-circuit an in-flight GROUP conversation past re-classification.

    A conversation's kind is fixed when it opens, but intent classification
    is stateless per message, so re-running it on a group follow-up (a bare
    "thanks", a terse aside) could pick EXPLAIN and silently break the turn
    out of its group. An existing GROUP thread therefore dispatches straight
    to group chat, reusing its roster.

    Returns:
        A GROUP outcome when ``conversation_id`` names a live GROUP thread,
        else ``None`` so the caller classifies normally.
    """
    if conversation_id is None:
        return None
    resume = app_state.slice(MetaStateSlice).conversational_resume_service
    if resume is None:
        return None
    conversation = await resume.get_conversation(conversation_id)
    if conversation is None or conversation.kind is not ConversationKind.GROUP:
        return None
    return IntentOutcome(
        intent=TurnIntent.GROUP_CONVENE,
        reason=IntentRoutingReason.CONVERSATION_KIND_FIXED,
    )


async def resolve_turn_intent(
    app_state: AppState,
    *,
    body: str,
    override: TurnIntent | None,
    conversation_id: NotBlankStr | None,
    named_targets: tuple[NotBlankStr, ...] = (),
) -> IntentOutcome:
    """Resolve the turn's intent: override, fixed kind, classifier, or default.

    ``named_targets`` are carried on the override path so a stream-deferred
    ACT/GROUP turn (which classified its targets on the stream, then re-issues
    buffered with an override) keeps those targets instead of losing them and
    degrading to EXPLAIN.

    Returns:
        The resolved :class:`IntentOutcome`. Falls back to EXPLAIN when no
        classifier is wired.
    """
    if override is not None:
        return IntentOutcome(
            intent=override,
            reason=IntentRoutingReason.EXPLICIT_OVERRIDE,
            named_targets=named_targets,
        )
    fixed = await _fixed_kind_intent(app_state, conversation_id)
    if fixed is not None:
        return fixed
    classifier = app_state.slice(MetaStateSlice).turn_intent_classifier
    if classifier is None:
        return IntentOutcome(
            intent=TurnIntent.EXPLAIN, reason=IntentRoutingReason.NO_INTENT_CLASSIFIER
        )
    return await classifier.classify(_classification_history(body, app_state))


__all__ = ["resolve_turn_intent"]
