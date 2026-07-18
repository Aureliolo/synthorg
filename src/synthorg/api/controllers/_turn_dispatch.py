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

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.api._feature_gate import ensure_feature_enabled
from synthorg.api.controllers._meta_chat_org_state import resolve_chat_org_state
from synthorg.api.controllers._meta_chat_routing import resolve_chat_answer
from synthorg.api.controllers._meta_chat_window import resolve_chat_snapshot_window
from synthorg.api.controllers._meta_signals_helpers import require_signals_service
from synthorg.api.controllers._turn_intent import resolve_turn_intent
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.hr.state import agent_registry_of
from synthorg.meta.charter.models import InterviewTurnArgs, InterviewTurnResult
from synthorg.meta.charter.state import CharterStateSlice
from synthorg.meta.chief_of_staff._multi_voice import ChimeIn
from synthorg.meta.chief_of_staff.actor import (
    ConversationalActArgs,
    ConversationalActResult,
)
from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
from synthorg.meta.chief_of_staff.group_models import (
    GroupConverseArgs,
    GroupConverseResult,
)
from synthorg.meta.chief_of_staff.intent_router import (
    IntentRoutingReason,
    TurnIntent,
)
from synthorg.meta.chief_of_staff.models import (
    ChatQuery,
    ChatResponse,
    ProposeArgs,
    ProposeResult,
)
from synthorg.meta.chief_of_staff.org_state import OrgStateSnapshot
from synthorg.meta.signal_models import OrgSignalSnapshot
from synthorg.meta.state import MetaStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.charter import CHARTER_SUBSTRATE_UNAVAILABLE
from synthorg.observability.events.chief_of_staff import (
    COS_MULTI_VOICE_FAILED,
    COS_TURN_DISPATCHED,
)
from synthorg.observability.events.meta import META_CHAT_DEPENDENCY_UNAVAILABLE
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)

_MESSAGE_MAX_LENGTH: Final[int] = 2000

# Intents that perform (or park) a side effect and therefore require org-mutation
# permission; EXPLAIN is a read any authenticated actor may run.
_SIDE_EFFECTING_INTENTS: Final[frozenset[TurnIntent]] = frozenset(
    {
        TurnIntent.PROPOSE,
        TurnIntent.GROUP_CONVENE,
        TurnIntent.ACT,
        TurnIntent.CHARTER,
    }
)


@dataclass(frozen=True)
class ExplainContext:
    """The resolved inputs an explain answer draws on (buffered or streamed).

    Attributes:
        chat_backend: The Chief of Staff chat backend to answer with.
        query: The user's question as a :class:`ChatQuery`.
        snapshot: The signal snapshot for grounding.
        org_state: The org-state read model, or ``None`` when persistence is
            disconnected (the answer degrades to "cannot see state").
    """

    chat_backend: ChiefOfStaffChat
    query: ChatQuery
    snapshot: OrgSignalSnapshot
    org_state: OrgStateSnapshot | None


@dataclass(frozen=True)
class TurnDispatchContext:
    """The resolved inputs every capability dispatch draws on.

    Built once by :func:`dispatch_turn` after the final (post-degradation)
    intent is known, so each ``_dispatch_*`` takes this one context instead of
    threading the same six values through its signature.

    Attributes:
        body: The operator's message for this turn.
        conversation_id: Existing conversation to continue, or ``None``.
        project: Project the turn is scoped to (propose / charter), or ``None``.
        actor_id: The authenticated actor issuing the turn.
        reason: Why this (final) intent was chosen or degraded to.
        confidence: Classifier confidence (0-1) when a classification ran;
            ``None`` for an override / no-classifier turn.
    """

    body: str
    conversation_id: NotBlankStr | None
    project: NotBlankStr | None
    actor_id: str
    reason: IntentRoutingReason
    confidence: float | None


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
    named_targets: tuple[NotBlankStr, ...] = Field(
        default=(),
        description=(
            "Roles/names the classifier read from the message, carried through a"
            " deferred stream so a re-issued ACT/GROUP turn keeps its targets"
            " instead of degrading to EXPLAIN. Only honoured with an override."
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
    chime_ins: tuple[ChimeIn, ...] = Field(
        default=(),
        description=(
            "Specialists who added a short attributed perspective to an "
            "explain answer; empty for every other intent."
        ),
    )

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


async def prepare_explain_context(app_state: AppState, *, body: str) -> ExplainContext:
    """Gate + resolve the pieces an explain answer draws on.

    Shared by the buffered explain dispatch and the streaming endpoint so both
    apply the same gate, backend requirement, and snapshot / org-state build.

    Returns:
        The chat backend, the query, the signal snapshot, and the org-state
        read model (``None`` when persistence is disconnected).

    Raises:
        ServiceUnavailableError: When the chat backend or signals service is
            not configured.
    """
    await ensure_feature_enabled(
        app_state, "chief_of_staff", "explain_chat_enabled", feature_label="Chat"
    )
    chat_backend = app_state.slice(MetaStateSlice).chief_of_staff_chat
    if chat_backend is None:
        logger.warning(
            META_CHAT_DEPENDENCY_UNAVAILABLE,
            dependency="chief_of_staff_chat",
            hint="Register an LLM provider so the chat backend can be built.",
        )
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
    return ExplainContext(
        chat_backend=chat_backend, query=query, snapshot=snapshot, org_state=org_state
    )


async def _dispatch_explain(
    app_state: AppState, ctx: TurnDispatchContext
) -> TurnResult:
    """Answer a read-only question about the org (the explain capability).

    The default and the safe fallback for every degraded or unclassified turn;
    also gathers any specialist chime-ins that clear the value bar.

    Returns:
        The turn result carrying the grounded answer and any chime-ins.

    Raises:
        ServiceUnavailableError: When the chat backend or signals service is
            not configured.
    """
    explain = await prepare_explain_context(app_state, body=ctx.body)
    answer = await resolve_chat_answer(
        app_state,
        explain.chat_backend,
        explain.query,
        explain.snapshot,
        explain.org_state,
    )
    chime_ins = await resolve_chime_ins(
        app_state, question=ctx.body, answer=answer.answer
    )
    return TurnResult(
        intent=TurnIntent.EXPLAIN,
        intent_reason=ctx.reason,
        intent_confidence=ctx.confidence,
        answer=answer,
        chime_ins=chime_ins,
    )


async def _dispatch_propose(
    app_state: AppState, ctx: TurnDispatchContext
) -> TurnResult:
    """Clarify a request or draft a plan (the propose capability).

    Returns:
        The turn result carrying the clarify-or-propose payload.

    Raises:
        ServiceUnavailableError: When the proposer is not configured.
    """
    await ensure_feature_enabled(
        app_state, "chief_of_staff", "propose_enabled", feature_label="Propose"
    )
    proposer = app_state.slice(MetaStateSlice).chief_of_staff_proposer
    if proposer is None:
        logger.warning(
            META_CHAT_DEPENDENCY_UNAVAILABLE,
            dependency="chief_of_staff_proposer",
            hint=(
                "Enable propose_enabled, register an LLM provider, and connect"
                " persistence."
            ),
        )
        msg = (
            "Chief of Staff propose is not configured. Enable "
            "``meta.chief_of_staff.propose_enabled`` in settings, register an "
            "LLM provider, and connect persistence."
        )
        raise ServiceUnavailableError(msg)
    propose = await proposer.converse(
        ProposeArgs(
            message=NotBlankStr(ctx.body),
            created_by=NotBlankStr(ctx.actor_id),
            conversation_id=ctx.conversation_id,
            project=ctx.project,
        )
    )
    return TurnResult(
        intent=TurnIntent.PROPOSE,
        intent_reason=ctx.reason,
        intent_confidence=ctx.confidence,
        conversation_id=propose.conversation_id,
        propose=propose,
    )


async def _dispatch_group(
    app_state: AppState,
    ctx: TurnDispatchContext,
    participants: tuple[NotBlankStr, ...],
) -> TurnResult:
    """Run one round of a multi-agent group discussion (group capability).

    Returns:
        The turn result carrying the group-round payload.

    Raises:
        ServiceUnavailableError: When the group chat service is not configured.
    """
    await ensure_feature_enabled(
        app_state, "chief_of_staff", "group_chat_enabled", feature_label="Group chat"
    )
    service = app_state.slice(MetaStateSlice).group_chat_service
    if service is None:
        logger.warning(
            META_CHAT_DEPENDENCY_UNAVAILABLE,
            dependency="group_chat_service",
            hint=(
                "Enable group_chat_enabled, register an LLM provider, configure"
                " agents, and connect persistence."
            ),
        )
        msg = (
            "Group chat is not configured. Enable "
            "``meta.chief_of_staff.group_chat_enabled`` in settings, register "
            "an LLM provider, configure agents, and connect persistence."
        )
        raise ServiceUnavailableError(msg)
    group = await service.converse(
        GroupConverseArgs(
            message=NotBlankStr(ctx.body),
            created_by=NotBlankStr(ctx.actor_id),
            conversation_id=ctx.conversation_id,
            participants=participants,
        )
    )
    return TurnResult(
        intent=TurnIntent.GROUP_CONVENE,
        intent_reason=ctx.reason,
        intent_confidence=ctx.confidence,
        conversation_id=group.conversation_id,
        group=group,
    )


async def _dispatch_act(
    app_state: AppState,
    ctx: TurnDispatchContext,
    agent: NotBlankStr,
) -> TurnResult:
    """Drive a real MCP action under trust (the act capability).

    Fail-closed and buffered: gated live on ``direct_mcp_enabled`` and never
    streamed, so a mid-run failure replays the cached result rather than
    re-executing already-run tools.

    Returns:
        The turn result carrying the direct-acting payload.

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
        logger.warning(
            META_CHAT_DEPENDENCY_UNAVAILABLE,
            dependency="conversational_actor",
            hint=(
                "Enable direct_mcp_enabled, register an LLM provider, and set"
                " security.mcp_self_consumer.mode to trust_scoped."
            ),
        )
        msg = (
            "Direct MCP acting is not configured. Enable "
            "``meta.chief_of_staff.direct_mcp_enabled`` in settings, register "
            "an LLM provider, and set "
            "``security.mcp_self_consumer.mode`` to ``trust_scoped``."
        )
        raise ServiceUnavailableError(msg)
    act = await actor_service.act(
        ConversationalActArgs(
            instruction=NotBlankStr(ctx.body),
            agent=agent,
            conversation_id=ctx.conversation_id,
            requested_by=ctx.actor_id,
        )
    )
    return TurnResult(
        intent=TurnIntent.ACT,
        intent_reason=ctx.reason,
        intent_confidence=ctx.confidence,
        conversation_id=ctx.conversation_id,
        act=act,
    )


async def _dispatch_charter(
    app_state: AppState, ctx: TurnDispatchContext
) -> TurnResult:
    """Run one charter-interview turn (the charter capability).

    Returns:
        The turn result carrying the interview payload (a question, or the
        drafted charter).

    Raises:
        ServiceUnavailableError: When the charter substrate is unavailable.
    """
    service = app_state.slice(CharterStateSlice).interview_service
    if service is None:
        logger.warning(
            CHARTER_SUBSTRATE_UNAVAILABLE,
            dependency="charter_interview_service",
            hint=(
                "Register an LLM provider and connect a persistence backend;"
                " complete setup first."
            ),
        )
        msg = (
            "Charter interview is unavailable: it needs an LLM provider and a "
            "connected persistence backend. Complete setup first."
        )
        raise ServiceUnavailableError(msg)
    charter = await service.run_turn(
        InterviewTurnArgs(
            message=NotBlankStr(wrap_untrusted(TAG_TASK_DATA, ctx.body)),
            created_by=NotBlankStr(ctx.actor_id),
            conversation_id=ctx.conversation_id,
            project=ctx.project,
        )
    )
    return TurnResult(
        intent=TurnIntent.CHARTER,
        intent_reason=ctx.reason,
        intent_confidence=ctx.confidence,
        conversation_id=charter.conversation_id,
        charter=charter,
    )


async def resolve_chime_ins(
    app_state: AppState, *, question: str, answer: str
) -> tuple[ChimeIn, ...]:
    """Gather specialist chime-ins for an explain answer, best-effort.

    Runs only when the multi-voice router is wired and ``multi_voice_enabled``
    is live-true (opt-out, default on). Never fails the turn: any error, or an
    empty roster, yields no chime-ins so the operator still gets the answer.

    Returns:
        The resolved chime-ins, strongest-first; empty when disabled,
        unwired, roster-empty, or the chime call fails.
    """
    router = app_state.slice(MetaStateSlice).multi_voice_router
    if router is None:
        return ()
    enabled = await resolve_bool_with_fallback(
        resolver=app_state.slice(SettingsStateSlice).config_resolver,
        namespace=SettingNamespace.CHIEF_OF_STAFF,
        key="multi_voice_enabled",
        fallback=True,
    )
    if not enabled:
        return ()
    try:
        active = tuple(await agent_registry_of(app_state).list_active())
        if not active:
            return ()
        return await router.chime(question=question, answer=answer, active=active)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised; chime is optional
        reraise_critical(exc)
        logger.warning(
            COS_MULTI_VOICE_FAILED,
            detail="chime_gather_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()


async def dispatch_turn(
    app_state: AppState,
    *,
    data: TurnRequest,
    actor_id: str,
    require_mutation: Callable[[], None],
) -> TurnResult:
    """Classify and dispatch one unified conversational turn.

    Resolves the intent (explicit override, classifier, or the EXPLAIN
    default), then dispatches to the owning capability. Who a group convenes,
    and which agent acts, come from the names the classifier reads out of the
    operator's own words: the operator talks to the org in plain language and
    never has to route a turn by hand. An act that names no agent, or a new
    group that names no participants, degrades to a plain answer so an
    ambiguous turn never acts or convenes on a guess.

    ``require_mutation`` is invoked (and may raise ``PermissionDeniedException``)
    once the final intent is known and is side-effecting, so a read-only actor
    can still run EXPLAIN while only propose/group/act/charter demand mutation
    permission.

    Returns:
        The unified :class:`TurnResult` carrying the resolved intent and its
        single capability payload.
    """
    body = data.message
    outcome = await resolve_turn_intent(
        app_state,
        body=body,
        override=data.intent_override,
        conversation_id=data.conversation_id,
        named_targets=data.named_targets,
    )
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

    # Enforce mutation permission on the FINAL (post-degradation) intent: a turn
    # that degraded to EXPLAIN is a read any authenticated actor may run, while a
    # surviving side-effecting intent requires org-mutation permission.
    if intent in _SIDE_EFFECTING_INTENTS:
        require_mutation()

    logger.info(
        COS_TURN_DISPATCHED,
        intent=intent.value,
        reason=reason.value,
        confidence=outcome.confidence,
    )

    ctx = TurnDispatchContext(
        body=body,
        conversation_id=data.conversation_id,
        project=data.project,
        actor_id=actor_id,
        reason=reason,
        confidence=outcome.confidence,
    )
    match intent:
        case TurnIntent.PROPOSE:
            return await _dispatch_propose(app_state, ctx)
        case TurnIntent.GROUP_CONVENE:
            return await _dispatch_group(app_state, ctx, participants)
        case TurnIntent.ACT:
            return await _dispatch_act(app_state, ctx, participants[0])
        case TurnIntent.CHARTER:
            return await _dispatch_charter(app_state, ctx)
        case _:
            return await _dispatch_explain(app_state, ctx)


__all__ = ["TurnRequest", "TurnResult", "dispatch_turn"]
