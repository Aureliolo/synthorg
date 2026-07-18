# module-kind: service
"""Concern routing for the conversational org interface.

Classifies each human turn to the best-fit role agent so a budget
question reaches the CFO, a strategy question reaches the CEO, and a
technical question reaches the senior technical role the company has
actually hired. A :class:`RoleRouter` returns a
:class:`~synthorg.meta.chief_of_staff.responder.RoutingOutcome`: a routed
decision when it routes confidently, else a fallback outcome whose
``reason`` explains why the generic Chief of Staff persona answers (no
active agents, classifier below the confidence floor, an unresolvable
role, and so on).

Two pluggable strategies, selected by ``ChiefOfStaffConfig.routing_strategy``:

- :class:`LlmConcernRouter` (default): a deterministic concern classifier
  over the live agent roster.
- :class:`KeywordRoleRouter`: a static keyword-to-role map, for
  deployments that prefer no extra LLM call.

Both share role resolution against the registry; the LLM classifier
fences the conversation history as untrusted content (via wrap_untrusted) and records
its spend through the cost chokepoint.
"""

import asyncio
import functools
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.communication.conversation.enums import ConversationRole
from synthorg.core.agent import AgentIdentity
from synthorg.core.authority import compare_authority
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.core.normalization import compare_ci, normalize_identifier
from synthorg.core.role_catalog import get_builtin_role
from synthorg.core.types import NotBlankStr, flatten_label
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.hr.registry import AgentRegistryService
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.enums import RoutingReason
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.meta.chief_of_staff.prompts import (
    CONCERN_ROUTING_SYSTEM,
    CONCERN_ROUTING_USER,
)
from synthorg.meta.chief_of_staff.responder import (
    RoutingDecision,
    RoutingOutcome,
    responder_for_identity,
)
from synthorg.meta.chief_of_staff.transcript import render_turns_transcript
from synthorg.observability import (
    get_logger,
    safe_error_description,
)
from synthorg.observability.events.chief_of_staff import (
    COS_ROUTING_FALLBACK,
    COS_ROUTING_RESPONSE_INVALID,
    COS_ROUTING_ROUTED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.kill_switch import resolve_str_with_fallback
from synthorg.settings.model_ref import parse_model_ref
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_ROUTING_AGENT_ID: NotBlankStr = NotBlankStr("system")
_ROUTING_TASK_ID: NotBlankStr = NotBlankStr("system:cos:routing")

# Static keyword -> role map for ``KeywordRoleRouter``. Scanned in order;
# the first group with any keyword present in the latest human message
# wins. Role names match catalog C-Suite roles; deployments with bespoke
# roles configure the LLM strategy instead.
_DEFAULT_KEYWORD_ROLE_MAP: tuple[tuple[tuple[str, ...], NotBlankStr], ...] = (
    (
        ("budget", "cost", "spend", "finance", "financial", "forecast"),
        NotBlankStr("CFO"),
    ),
    (
        ("strategy", "strategic", "vision", "mission", "market", "competitor"),
        NotBlankStr("CEO"),
    ),
    (
        ("technical", "architecture", "engineering", "infrastructure", "codebase"),
        NotBlankStr("CTO"),
    ),
    (
        ("product", "feature", "roadmap", "backlog"),
        NotBlankStr("CPO"),
    ),
    (
        ("operations", "process", "logistics", "delivery"),
        NotBlankStr("COO"),
    ),
)


class ConcernClassification(BaseModel):
    """Structured output of one concern-classification model turn.

    Attributes:
        topic: Short lower-case concern label (e.g. ``"budget"``).
        role: Role name copied from a candidate; resolved against the
            active roster.
        confidence: Classifier confidence (0-1) that ``role`` is the
            best fit.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    topic: NotBlankStr
    role: NotBlankStr
    confidence: float = Field(ge=0.0, le=1.0)


@runtime_checkable
class RoleRouter(Protocol):
    """Routes one human turn to a role agent, or to the generic persona.

    Implementations are best-effort: ``route()`` always returns a
    :class:`RoutingOutcome`. Any uncertainty (no active agents, low
    confidence, unresolvable role, classifier error) yields an outcome
    with ``decision=None`` so the conversation is still answered by the
    generic Chief of Staff.
    """

    async def route(self, history: tuple[ConversationTurn, ...]) -> RoutingOutcome:
        """Decide the responder for the latest human turn.

        Args:
            history: Conversation turns oldest-first, ending with the
                human turn to route.

        Returns:
            A :class:`RoutingOutcome` carrying the routed decision (or
            ``None`` to fall back to the generic Chief of Staff) plus the
            reason the outcome landed.
        """
        ...


def _by_seniority_then_name(a: AgentIdentity, b: AgentIdentity) -> int:
    """Order two role-holders most-senior-first, then name ascending.

    Returns:
        Negative when *a* sorts before *b*, positive when after, zero
        when identical on both keys.
    """
    by_authority = compare_authority(b.role, a.role)
    if by_authority != 0:
        return by_authority
    return (a.name > b.name) - (a.name < b.name)


def _resolve_agent_for_role(
    active: tuple[AgentIdentity, ...], role: NotBlankStr
) -> AgentIdentity | None:
    """Find the active agent holding *role* (case-insensitive).

    When several active agents share a role, the most senior is chosen
    (the natural primary for the role), with the alphabetically-first by
    name as a deterministic tiebreak across equal-seniority holders.

    Returns:
        The matching identity, or ``None`` when no active agent holds it.
    """
    matches = [a for a in active if compare_ci(a.role, role)]
    if not matches:
        return None
    return min(matches, key=functools.cmp_to_key(_by_seniority_then_name))


def _render_candidate_roles(active: tuple[AgentIdentity, ...]) -> str:
    """Render the distinct active roles as a classifier candidate list.

    Role names and catalog skills are system-controlled, so the roster is
    not wrapped as untrusted content (it is the very menu the classifier
    must choose from). The operator-configured role / department labels
    are still flattened via :func:`flatten_label` as defence in
    depth. One line per distinct role, enriched with the catalog's
    required skills or description when the role is a builtin.

    Returns:
        The newline-joined candidate roster.
    """
    seen: set[str] = set()
    lines: list[str] = []
    for identity in sorted(active, key=lambda a: a.role):
        key = normalize_identifier(identity.role)
        if key in seen:
            continue
        seen.add(key)
        role = flatten_label(identity.role)
        department = flatten_label(identity.department)
        catalog = get_builtin_role(identity.role)
        if catalog is not None and catalog.required_skills:
            descriptor = ", ".join(catalog.required_skills)
            lines.append(f"- {role} ({department}): {descriptor}")
        elif catalog is not None and catalog.description:
            lines.append(f"- {role} ({department}): {catalog.description}")
        else:
            lines.append(f"- {role} ({department})")
    return "\n".join(lines)


def _latest_human_text(history: tuple[ConversationTurn, ...]) -> str | None:
    """Return the content of the most recent human turn, if any.

    Returns:
        The latest ``USER`` turn content, or ``None`` when the history
        carries no human turn.
    """
    for turn in reversed(history):
        if turn.role is ConversationRole.USER:
            return turn.content
    return None


class LlmConcernRouter:
    """Routes a turn via a deterministic concern classifier.

    Builds a candidate roster from the live agent registry, asks the
    classifier model to pick the single best-fit role with a confidence
    score, and resolves that role to an active agent. Falls back to the
    generic responder (returns ``None``) on any uncertainty.

    Args:
        provider: Completion provider for the classification call.
        model: Classifier model identifier.
        agent_registry: Source of the candidate roster and resolution.
        confidence_floor: Minimum confidence to route to a role.
        default_role: Role to try when a confident classification names
            a role with no active agent.
        temperature: Classifier sampling temperature.
        max_tokens: Token budget for one classification reply.
        timeout_seconds: Wall-clock cap for the classification call. The
            call runs under the per-conversation lock before each turn, so
            this bound stops a hung provider from stalling the conversation;
            on timeout the router falls back to the generic responder.
        cost_tracker: Optional cost tracker for the classification call.
        provider_registry: Source registry for the live ``routing_model``
            re-read, so a per-call retarget resolves BOTH the provider and
            model from the same ref. Absent, the classifier stays on the
            build-time pair.
    """

    _PURPOSE_ID: ClassVar[PromptPurposeId] = PromptPurposeId.COS_ROUTING

    @property
    def metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for this prompt class."""
        return pin_for(self._PURPOSE_ID)

    def __init__(  # noqa: PLR0913 -- DI seam: independently-wired knobs
        self,
        *,
        provider: CompletionProvider,
        model: NotBlankStr,
        agent_registry: AgentRegistryService,
        confidence_floor: float,
        default_role: NotBlankStr,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        cost_tracker: CostTrackerProtocol | None = None,
        provider_registry: ProviderRegistry | None = None,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._agent_registry = agent_registry
        self._confidence_floor = confidence_floor
        self._default_role = default_role
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds
        self._cost_tracker = cost_tracker
        self._provider_registry = provider_registry
        self._config_resolver = config_resolver

    async def route(self, history: tuple[ConversationTurn, ...]) -> RoutingOutcome:
        """Classify the latest human turn and resolve a role agent.

        Returns:
            The routed outcome, or a fallback outcome carrying the reason
            the generic Chief of Staff answers instead.
        """
        active = await self._agent_registry.list_active()
        if not active:
            logger.info(COS_ROUTING_FALLBACK, detail="no_active_agents")
            return RoutingOutcome(reason=RoutingReason.NO_ACTIVE_AGENTS)
        classification = await self._classify(history, active)
        if isinstance(classification, RoutingReason):
            return RoutingOutcome(reason=classification)
        if classification.confidence < self._confidence_floor:
            logger.info(
                COS_ROUTING_FALLBACK,
                detail="below_confidence_floor",
                topic=classification.topic,
                role=classification.role,
                confidence=classification.confidence,
            )
            return RoutingOutcome(reason=RoutingReason.BELOW_CONFIDENCE_FLOOR)
        agent = _resolve_agent_for_role(active, classification.role)
        if agent is None:
            agent = _resolve_agent_for_role(active, self._default_role)
        if agent is None:
            logger.info(
                COS_ROUTING_FALLBACK,
                detail="role_unresolved",
                topic=classification.topic,
                role=classification.role,
            )
            return RoutingOutcome(reason=RoutingReason.ROLE_UNRESOLVED)
        logger.info(
            COS_ROUTING_ROUTED,
            topic=classification.topic,
            role=agent.role,
            agent_id=str(agent.id),
            confidence=classification.confidence,
        )
        return RoutingOutcome(
            reason=RoutingReason.ROUTED,
            decision=RoutingDecision(
                responder=responder_for_identity(agent),
                topic=classification.topic,
                confidence=classification.confidence,
            ),
        )

    async def _resolve_live_dispatch(self) -> tuple[CompletionProvider, str]:
        """Resolve the live ``(provider, model)`` pair for one classify call.

        Re-reads ``chief_of_staff.routing_model`` so an operator retargets it
        without a restart, resolving BOTH halves from the SAME ref -- a newly
        chosen model can never dispatch on the previously bound provider. A
        live ref that is absent, provider-less, unregistered, or missing a
        model id falls back to the build-time pair, matched by construction.

        Returns:
            The provider driver and model id to dispatch this call on.
        """
        if self._config_resolver is None or self._provider_registry is None:
            return self._provider, self._model
        raw_ref = await resolve_str_with_fallback(
            resolver=self._config_resolver,
            namespace=SettingNamespace.CHIEF_OF_STAFF,
            key="routing_model",
            fallback="",
        )
        if not raw_ref:
            return self._provider, self._model
        ref = parse_model_ref(raw_ref)
        if not ref.provider or not ref.model_id:
            return self._provider, self._model
        try:
            driver = self._provider_registry.get(ref.provider)
        except DriverNotRegisteredError:
            return self._provider, self._model
        return driver, ref.model_id

    async def _classify(
        self,
        history: tuple[ConversationTurn, ...],
        active: tuple[AgentIdentity, ...],
    ) -> ConcernClassification | RoutingReason:
        """Run one classification call and parse its structured output.

        Routing is best-effort, so a classifier hiccup degrades to the
        generic responder rather than blocking the conversation; the
        specific failure is returned as a :class:`RoutingReason` so the
        caller can surface why.

        Returns:
            The parsed classification, or the fallback reason on a call
            failure (``CLASSIFY_CALL_FAILED``) or invalid response
            (``RESPONSE_INVALID``).
        """
        user = CONCERN_ROUTING_USER.format(
            candidate_roles=_render_candidate_roles(active),
            conversation_history=wrap_untrusted(
                TAG_TASK_DATA, render_turns_transcript(history)
            ),
        )
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=CONCERN_ROUTING_SYSTEM),
            ChatMessage(role=MessageRole.USER, content=user),
        ]
        completion_config = CompletionConfig(
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        provider, model = await self._resolve_live_dispatch()
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=_ROUTING_AGENT_ID,
                task_id=_ROUTING_TASK_ID,
                purpose=self.metadata.prompt_class_id,
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await asyncio.wait_for(
                    provider.complete(
                        messages,
                        model,
                        config=completion_config,
                    ),
                    timeout=self._timeout_seconds,
                )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Routing is best-effort: any classifier failure degrades to the
            # generic responder rather than propagating. Preserve the
            # provider-error classification (error_type) plus any backoff
            # hint (retry_after) so a repeated rate-limit driving the
            # fallback stays diagnosable instead of collapsing to an
            # untyped signal.
            logger.warning(
                COS_ROUTING_FALLBACK,
                detail="classify_call_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                retry_after=getattr(exc, "retry_after", None),
            )
            return RoutingReason.CLASSIFY_CALL_FAILED
        raw = (response.content or "").strip()
        parsed = extract_json_from_llm_response(
            raw,
            logger_callback=lambda detail: logger.warning(
                COS_ROUTING_RESPONSE_INVALID, detail=detail
            ),
        )
        if parsed is None:
            return RoutingReason.RESPONSE_INVALID
        try:
            return ConcernClassification.model_validate(parsed)
        except ValidationError as exc:
            logger.warning(
                COS_ROUTING_RESPONSE_INVALID,
                detail="schema_validation_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return RoutingReason.RESPONSE_INVALID


class KeywordRoleRouter:
    """Routes a turn via a static keyword-to-role map (no LLM call).

    Scans the latest human turn for the first keyword group whose role
    resolves to an active agent. A match routes with full confidence; no
    match (or an unresolved role) falls back to the generic responder.

    Args:
        agent_registry: Source for role-to-agent resolution.
        default_role: Role to try when a matched keyword's role has no
            active agent.
        keyword_map: Override for the default keyword-to-role groups.
    """

    def __init__(
        self,
        *,
        agent_registry: AgentRegistryService,
        default_role: NotBlankStr,
        keyword_map: tuple[
            tuple[tuple[str, ...], NotBlankStr], ...
        ] = _DEFAULT_KEYWORD_ROLE_MAP,
    ) -> None:
        self._agent_registry = agent_registry
        self._default_role = default_role
        self._keyword_map = keyword_map

    async def route(self, history: tuple[ConversationTurn, ...]) -> RoutingOutcome:
        """Map keywords in the latest human turn to a role agent.

        Returns:
            A routed outcome with confidence ``1.0`` on a keyword match,
            or a fallback outcome carrying the reason otherwise.
        """
        text = _latest_human_text(history)
        if text is None:
            return RoutingOutcome(reason=RoutingReason.NO_KEYWORD_MATCH)
        lowered = text.casefold()
        active = await self._agent_registry.list_active()
        if not active:
            logger.info(COS_ROUTING_FALLBACK, detail="no_active_agents")
            return RoutingOutcome(reason=RoutingReason.NO_ACTIVE_AGENTS)
        for keywords, role in self._keyword_map:
            matched = next((kw for kw in keywords if kw in lowered), None)
            if matched is None:
                continue
            agent = _resolve_agent_for_role(active, role)
            if agent is None:
                agent = _resolve_agent_for_role(active, self._default_role)
            if agent is None:
                continue
            logger.info(
                COS_ROUTING_ROUTED,
                topic=matched,
                role=agent.role,
                agent_id=str(agent.id),
                strategy="keyword",
            )
            return RoutingOutcome(
                reason=RoutingReason.ROUTED,
                decision=RoutingDecision(
                    responder=responder_for_identity(agent),
                    topic=NotBlankStr(matched),
                    confidence=1.0,
                ),
            )
        logger.info(COS_ROUTING_FALLBACK, detail="no_keyword_match")
        return RoutingOutcome(reason=RoutingReason.NO_KEYWORD_MATCH)


def _resolve_router_provider(
    provider_registry: ProviderRegistry, model: str
) -> tuple[CompletionProvider | None, str]:
    """Resolve the provider the concern-routing model ref is bound to.

    The routing model is an explicit ``(provider, model)`` ref: a bare id is
    never auto-resolved against "whichever provider serves it", so a
    provider-less setting leaves the router unbuilt (routing degrades to the
    generic responder) rather than binding to an arbitrary gateway. The ref is
    parsed once here and its ``model_id`` returned so the caller need not
    re-parse.

    Returns:
        A ``(driver, model_id)`` pair. ``driver`` is ``None`` when the ref
        names no provider or an unregistered one; ``model_id`` is always the
        ref's model id (possibly empty) for the caller to validate.
    """
    from synthorg.providers.errors import DriverNotRegisteredError  # noqa: PLC0415
    from synthorg.settings.model_ref import parse_model_ref  # noqa: PLC0415

    ref = parse_model_ref(model)
    if not ref.provider:
        logger.warning(
            COS_ROUTING_FALLBACK,
            detail="routing_model_has_no_provider",
            model=ref.model_id,
        )
        return None, ref.model_id
    try:
        return provider_registry.get(ref.provider), ref.model_id
    except DriverNotRegisteredError:
        logger.warning(
            COS_ROUTING_FALLBACK,
            detail="no_provider_for_routing_model",
            model=ref.model_id,
            provider=ref.provider,
        )
        return None, ref.model_id


def build_role_router(
    *,
    config: ChiefOfStaffConfig,
    provider_registry: ProviderRegistry,
    agent_registry: AgentRegistryService,
    cost_tracker: CostTrackerProtocol | None = None,
    config_resolver: ConfigResolver | None = None,
) -> RoleRouter | None:
    """Build the configured :class:`RoleRouter`, or ``None`` when unbuildable.

    The router is built unconditionally of ``routing_enabled`` (the proposer
    gates routing per turn on the live flag, so the instance must exist for
    the flag to flip on without a restart). Returns ``None`` only when the
    classifier provider is absent for the LLM strategy.

    Args:
        config: Chief of Staff configuration.
        provider_registry: Source of the classifier provider (LLM
            strategy only).
        agent_registry: Source of the candidate roster and resolution.
        cost_tracker: Optional cost tracker for classification calls.
        config_resolver: Optional resolver for the live ``routing_model``
            read in the LLM classifier.

    Returns:
        A router, or ``None`` when the classifier provider is absent.
    """
    if config.routing_strategy == "keyword":
        keyword_map = (
            tuple((rule.keywords, rule.role) for rule in config.routing_keyword_rules)
            if config.routing_keyword_rules
            else _DEFAULT_KEYWORD_ROLE_MAP
        )
        return KeywordRoleRouter(
            agent_registry=agent_registry,
            default_role=config.routing_default_role,
            keyword_map=keyword_map,
        )
    routing_model = config.routing_model
    if not routing_model:
        logger.info(COS_ROUTING_FALLBACK, detail="routing_model_not_configured")
        return None
    # The ref is parsed once inside ``_resolve_router_provider``, which returns
    # the model id (never the raw ``{provider, model_id}`` JSON; the router
    # re-reads the live ref via the resolver).
    provider, model_id = _resolve_router_provider(provider_registry, routing_model)
    if provider is None:
        return None
    if not model_id:
        logger.warning(
            COS_ROUTING_FALLBACK,
            detail="routing_model_has_no_model_id",
        )
        return None
    return LlmConcernRouter(
        provider=provider,
        model=NotBlankStr(model_id),
        agent_registry=agent_registry,
        confidence_floor=config.routing_confidence_floor,
        default_role=config.routing_default_role,
        temperature=config.routing_temperature,
        max_tokens=config.routing_max_tokens,
        timeout_seconds=config.agent_call_timeout_seconds,
        cost_tracker=cost_tracker,
        provider_registry=provider_registry,
        config_resolver=config_resolver,
    )


__all__ = [
    "ConcernClassification",
    "KeywordRoleRouter",
    "LlmConcernRouter",
    "RoleRouter",
    "build_role_router",
]
