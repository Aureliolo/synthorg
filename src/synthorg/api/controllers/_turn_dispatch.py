# module-kind: service
"""Unified conversational turn dispatch.

One entry point behind ``POST /meta/chat/turn``: classify what an operator's
message wants, then dispatch it to the capability that already implements it
(explain / propose / group / act / charter). The capability *services* are
unchanged; this layer only picks which one answers, so collapsing the five
mode endpoints into one surface never collapses the state machines beneath.

The intent classifier lives in the meta layer; the per-capability dispatch
here lives in the API layer because it composes the same app-state-resolved
helpers, gates, and services the five original controllers used. Each branch
re-checks its own capability gate, so an ACT turn on a message while
``direct_mcp_enabled`` is off fails closed exactly as the old ``/meta/chat/act``
did, never silently downgraded to a read.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.api._feature_gate import ensure_feature_enabled
from synthorg.api.controllers._meta_chat_org_state import resolve_chat_org_state
from synthorg.api.controllers._meta_chat_routing import resolve_chat_answer
from synthorg.api.controllers._meta_chat_window import resolve_chat_snapshot_window
from synthorg.api.controllers._meta_signals_helpers import require_signals_service
from synthorg.api.state import AppState
from synthorg.communication.conversation.enums import ConversationRole
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.meta.charter.models import InterviewTurnArgs, InterviewTurnResult
from synthorg.meta.charter.state import CharterStateSlice
from synthorg.meta.chief_of_staff.actor import (
    ConversationalActArgs,
    ConversationalActResult,
)
from synthorg.meta.chief_of_staff.group_models import (
    GroupConverseArgs,
    GroupConverseResult,
)
from synthorg.meta.chief_of_staff.intent_router import (
    IntentOutcome,
    IntentRoutingReason,
    TurnIntent,
)
from synthorg.meta.chief_of_staff.models import (
    ChatQuery,
    ChatResponse,
    ConversationTurn,
    ProposeArgs,
    ProposeResult,
)
from synthorg.meta.state import MetaStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.chief_of_staff import COS_TURN_DISPATCHED

logger = get_logger(__name__)

_MESSAGE_MAX_LENGTH: int = 2000


class TurnRequest(BaseModel):
    """Request body for one unified conversational turn."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    message: NotBlankStr = Field(
        max_length=_MESSAGE_MAX_LENGTH,
        description="The operator's message for this turn.",
    )
    conversation_id: NotBlankStr | None = Field(
        default=None,
        description="Existing conversation to continue; None starts a new one.",
    )
    intent_override: TurnIntent | None = Field(
        default=None,
        description=(
            "Force a capability instead of classifying (e.g. to continue a"
            " typed conversation). None auto-routes."
        ),
    )
    project: NotBlankStr | None = Field(
        default=None,
        description="Project the turn is scoped to, for propose/charter turns.",
    )


class TurnResult(BaseModel):
    """Outcome of one unified turn: the resolved intent plus its payload.

    Exactly one capability payload is set, matching :attr:`intent` (a degraded
    or explain turn carries :attr:`answer`).

    Attributes:
        intent: The capability the turn dispatched to.
        intent_reason: Why this intent was chosen or degraded to.
        intent_confidence: Classifier confidence (0-1) when a classification
            ran; ``None`` for an override / no-classifier turn.
        conversation_id: The conversation this turn belongs to; ``None`` for
            the stateless explain path.
        answer: The explain answer (set iff ``intent`` is EXPLAIN).
        propose: The clarify-or-propose outcome (set iff PROPOSE).
        group: The group-round outcome (set iff GROUP_CONVENE).
        act: The direct-acting outcome (set iff ACT).
        charter: The charter-interview outcome (set iff CHARTER).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    intent: TurnIntent
    intent_reason: IntentRoutingReason
    intent_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    conversation_id: NotBlankStr | None = None
    answer: ChatResponse | None = None
    propose: ProposeResult | None = None
    group: GroupConverseResult | None = None
    act: ConversationalActResult | None = None
    charter: InterviewTurnResult | None = None

    @model_validator(mode="after")
    def _validate_single_payload(self) -> Self:
        """Enforce exactly-one payload set, matching ``intent``.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: When the set payload does not match ``intent``, or a
                turn carries zero or several payloads.
        """
        payloads = {
            TurnIntent.EXPLAIN: self.answer,
            TurnIntent.PROPOSE: self.propose,
            TurnIntent.GROUP_CONVENE: self.group,
            TurnIntent.ACT: self.act,
            TurnIntent.CHARTER: self.charter,
        }
        present = [
            intent for intent, payload in payloads.items() if payload is not None
        ]
        if present != [self.intent]:
            msg = (
                f"exactly the {self.intent.value!r} payload must be set; "
                f"got {[i.value for i in present]}"
            )
            raise ValueError(msg)
        return self


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


async def _resolve_intent(
    app_state: AppState,
    *,
    body: str,
    override: TurnIntent | None,
) -> IntentOutcome:
    """Resolve the turn's intent: explicit override, classifier, or default.

    Returns:
        The resolved :class:`IntentOutcome`. Falls back to EXPLAIN when no
        classifier is wired.
    """
    if override is not None:
        return IntentOutcome(
            intent=override, reason=IntentRoutingReason.EXPLICIT_OVERRIDE
        )
    classifier = app_state.slice(MetaStateSlice).turn_intent_classifier
    if classifier is None:
        return IntentOutcome(
            intent=TurnIntent.EXPLAIN, reason=IntentRoutingReason.NO_INTENT_CLASSIFIER
        )
    return await classifier.classify(_classification_history(body, app_state))


async def _dispatch_explain(
    app_state: AppState, *, body: str, project: NotBlankStr | None
) -> ChatResponse:
    """Answer a read-only question about the org (the explain capability).

    Returns:
        The grounded chat answer.

    Raises:
        ServiceUnavailableError: When the chat backend or signals service is
            not configured.
    """
    del project  # explain is not project-scoped
    await ensure_feature_enabled(
        app_state, "chief_of_staff", "explain_chat_enabled", feature_label="Chat"
    )
    chat_backend = app_state.slice(MetaStateSlice).chief_of_staff_chat
    if chat_backend is None:
        msg = (
            "Chief of Staff chat is not configured. Register an LLM provider "
            "so the chat backend can be built."
        )
        raise ServiceUnavailableError(msg)
    signals_service = require_signals_service(
        app_state, "SignalsService is not configured; cannot build a snapshot."
    )
    snapshot = await signals_service.get_org_snapshot(
        since=app_state.clock.now() - await resolve_chat_snapshot_window(app_state),
    )
    org_state = await resolve_chat_org_state(app_state)
    query = ChatQuery(question=NotBlankStr(body))
    return await resolve_chat_answer(
        app_state, chat_backend, query, snapshot, org_state
    )


async def _dispatch_propose(
    app_state: AppState,
    *,
    body: str,
    conversation_id: NotBlankStr | None,
    project: NotBlankStr | None,
    actor_id: str,
) -> ProposeResult:
    """Clarify a request or draft a plan (the propose capability).

    Returns:
        The clarify-or-propose outcome.

    Raises:
        ServiceUnavailableError: When the proposer is not configured.
    """
    await ensure_feature_enabled(
        app_state, "chief_of_staff", "propose_enabled", feature_label="Propose"
    )
    proposer = app_state.slice(MetaStateSlice).chief_of_staff_proposer
    if proposer is None:
        msg = (
            "Chief of Staff propose is not configured. Enable "
            "``meta.chief_of_staff.propose_enabled`` in settings, register an "
            "LLM provider, and connect persistence."
        )
        raise ServiceUnavailableError(msg)
    return await proposer.converse(
        ProposeArgs(
            message=NotBlankStr(body),
            created_by=NotBlankStr(actor_id),
            conversation_id=conversation_id,
            project=project,
        )
    )


async def _dispatch_group(
    app_state: AppState,
    *,
    body: str,
    conversation_id: NotBlankStr | None,
    participants: tuple[NotBlankStr, ...],
    actor_id: str,
) -> GroupConverseResult:
    """Run one round of a multi-agent group discussion (group capability).

    Returns:
        The group-round outcome.

    Raises:
        ServiceUnavailableError: When the group chat service is not configured.
    """
    await ensure_feature_enabled(
        app_state, "chief_of_staff", "group_chat_enabled", feature_label="Group chat"
    )
    service = app_state.slice(MetaStateSlice).group_chat_service
    if service is None:
        msg = (
            "Group chat is not configured. Enable "
            "``meta.chief_of_staff.group_chat_enabled`` in settings, register "
            "an LLM provider, configure agents, and connect persistence."
        )
        raise ServiceUnavailableError(msg)
    return await service.converse(
        GroupConverseArgs(
            message=NotBlankStr(body),
            created_by=NotBlankStr(actor_id),
            conversation_id=conversation_id,
            participants=participants,
        )
    )


async def _dispatch_act(
    app_state: AppState,
    *,
    body: str,
    agent: NotBlankStr,
    conversation_id: NotBlankStr | None,
    actor_id: str,
) -> ConversationalActResult:
    """Drive a real MCP action under trust (the act capability).

    Fail-closed and buffered: gated live on ``direct_mcp_enabled`` and never
    streamed, so a mid-run failure replays the cached result rather than
    re-executing already-run tools.

    Returns:
        The direct-acting outcome.

    Raises:
        ServiceUnavailableError: When the actor is not configured.
    """
    await ensure_feature_enabled(
        app_state,
        "chief_of_staff",
        "direct_mcp_enabled",
        feature_label="Direct MCP acting",
    )
    actor_service = app_state.slice(MetaStateSlice).conversational_actor
    if actor_service is None:
        msg = (
            "Direct MCP acting is not configured. Enable "
            "``meta.chief_of_staff.direct_mcp_enabled`` in settings, register "
            "an LLM provider, and set "
            "``security.mcp_self_consumer.mode`` to ``trust_scoped``."
        )
        raise ServiceUnavailableError(msg)
    return await actor_service.act(
        ConversationalActArgs(
            instruction=NotBlankStr(body),
            agent=agent,
            conversation_id=conversation_id,
            requested_by=actor_id,
        )
    )


async def _dispatch_charter(
    app_state: AppState,
    *,
    body: str,
    conversation_id: NotBlankStr | None,
    project: NotBlankStr | None,
    actor_id: str,
) -> InterviewTurnResult:
    """Run one charter-interview turn (the charter capability).

    Returns:
        The interview turn outcome (a question, or the drafted charter).

    Raises:
        ServiceUnavailableError: When the charter substrate is unavailable.
    """
    service = app_state.slice(CharterStateSlice).interview_service
    if service is None:
        msg = (
            "Charter interview is unavailable: it needs an LLM provider and a "
            "connected persistence backend. Complete setup first."
        )
        raise ServiceUnavailableError(msg)
    return await service.run_turn(
        InterviewTurnArgs(
            message=NotBlankStr(wrap_untrusted(TAG_TASK_DATA, body)),
            created_by=NotBlankStr(actor_id),
            conversation_id=conversation_id,
            project=project,
        )
    )


async def dispatch_turn(
    app_state: AppState,
    *,
    data: TurnRequest,
    actor_id: str,
) -> TurnResult:
    """Classify and dispatch one unified conversational turn.

    Resolves the intent (explicit override, classifier, or the EXPLAIN
    default), then dispatches to the owning capability. Who a group convenes,
    and which agent acts, come from the names the classifier reads out of the
    operator's own words: the operator talks to the org in plain language and
    never has to route a turn by hand. An act that names no agent, or a new
    group that names no participants, degrades to a plain answer so an
    ambiguous turn never acts or convenes on a guess.

    Returns:
        The unified :class:`TurnResult` carrying the resolved intent and its
        single capability payload.
    """
    body = data.message
    outcome = await _resolve_intent(app_state, body=body, override=data.intent_override)
    intent = outcome.intent
    reason = outcome.reason
    # The acting agent and the group roster are the names the classifier read
    # from the message; the concern router still picks the answering voice for
    # explain/propose, so the operator never routes a turn by hand.
    participants = outcome.named_targets

    if intent is TurnIntent.ACT and not participants:
        intent, reason = TurnIntent.EXPLAIN, IntentRoutingReason.ACT_NO_TARGET
    # A new group needs named participants to open; continuing an existing
    # group conversation reuses its roster, so only degrade when opening.
    if (
        intent is TurnIntent.GROUP_CONVENE
        and not participants
        and data.conversation_id is None
    ):
        intent, reason = TurnIntent.EXPLAIN, IntentRoutingReason.GROUP_TARGETS_MISSING

    logger.info(
        COS_TURN_DISPATCHED,
        intent=intent.value,
        reason=reason.value,
        confidence=outcome.confidence,
    )

    confidence = outcome.confidence
    match intent:
        case TurnIntent.PROPOSE:
            propose = await _dispatch_propose(
                app_state,
                body=body,
                conversation_id=data.conversation_id,
                project=data.project,
                actor_id=actor_id,
            )
            return TurnResult(
                intent=intent,
                intent_reason=reason,
                intent_confidence=confidence,
                conversation_id=propose.conversation_id,
                propose=propose,
            )
        case TurnIntent.GROUP_CONVENE:
            group = await _dispatch_group(
                app_state,
                body=body,
                conversation_id=data.conversation_id,
                participants=participants,
                actor_id=actor_id,
            )
            return TurnResult(
                intent=intent,
                intent_reason=reason,
                intent_confidence=confidence,
                conversation_id=group.conversation_id,
                group=group,
            )
        case TurnIntent.ACT:
            act = await _dispatch_act(
                app_state,
                body=body,
                agent=participants[0],
                conversation_id=data.conversation_id,
                actor_id=actor_id,
            )
            return TurnResult(
                intent=intent,
                intent_reason=reason,
                intent_confidence=confidence,
                conversation_id=data.conversation_id,
                act=act,
            )
        case TurnIntent.CHARTER:
            charter = await _dispatch_charter(
                app_state,
                body=body,
                conversation_id=data.conversation_id,
                project=data.project,
                actor_id=actor_id,
            )
            return TurnResult(
                intent=intent,
                intent_reason=reason,
                intent_confidence=confidence,
                conversation_id=charter.conversation_id,
                charter=charter,
            )
        case _:
            # EXPLAIN is the default and the safe fallback for every degraded
            # or unclassified turn.
            answer = await _dispatch_explain(app_state, body=body, project=data.project)
            return TurnResult(
                intent=intent,
                intent_reason=reason,
                intent_confidence=confidence,
                answer=answer,
            )


__all__ = ["TurnRequest", "TurnResult", "dispatch_turn"]
