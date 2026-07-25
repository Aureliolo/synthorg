# module-kind: service
"""Grounded agent reply to an operator's comment on a plan item.

When an operator comments on an item of a plan under review, the responsible
role answers inline: the item's owner if an active agent holds it, otherwise
the Chief of Staff. The reply is one bounded, grounded completion call (not a
tool-using session) over the item's own text, so the answer stays anchored to
what is under review rather than wandering.

Best-effort by construction: a reply never fails or blocks the operator's
comment. Any model error, empty answer, or resolution miss yields no reply, so
the operator's comment still posts. Loop-safe: only a human comment is answered
(the caller never feeds an agent reply back in), so a reply cannot trigger
another. Mirrors the multi-voice router's provider-binding and best-effort
discipline.
"""

import asyncio
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.meta.chief_of_staff._role_resolution import resolve_agent_for_role
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.plan_review import (
    PLAN_REVIEW_REPLY_ADDED,
    PLAN_REVIEW_REPLY_FAILED,
    PLAN_REVIEW_REPLY_SKIPPED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.kill_switch import (
    resolve_float_with_fallback,
    resolve_int_with_fallback,
    resolve_str_with_fallback,
)
from synthorg.settings.model_ref import parse_model_ref
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

#: Attribution for the generic Chief-of-Staff fallback reply: not a hired agent,
#: so it carries the system persona id rather than a roster agent id.
_COS_NAME: NotBlankStr = NotBlankStr("Chief of Staff")
_COS_AGENT_ID: NotBlankStr = NotBlankStr("chief-of-staff")

_REPLY_AGENT_ID: NotBlankStr = NotBlankStr("system")
_REPLY_TASK_ID: NotBlankStr = NotBlankStr("system:plan_review:item_reply")

_REPLY_SYSTEM = "\n".join(
    [
        "You are {role} ({name}), answering an operator's question about one",
        "item in a plan your organisation is reviewing before it commits.",
        "Answer concretely and briefly, in your own role's voice, grounded",
        "ONLY in the item shown below. If the item does not carry what you'd",
        "need to answer, say plainly what is missing rather than inventing it.",
        "Do not restate the whole item; respond to the operator's point.",
        untrusted_content_directive((TAG_TASK_DATA,)),
    ]
)

_REPLY_USER = (
    "Plan objective: {objective}\n"
    "\n"
    "Item under discussion:\n"
    "{item}\n"
    "\n"
    "The operator's comment:\n"
    "{comment}"
)


class AgentReply(BaseModel):
    """One responsible role's grounded reply to a plan-item comment.

    Attributes:
        author: The replying agent's display name (or the Chief of Staff).
        author_agent_id: The replying agent's id (a system id for the CoS
            fallback), so the reply is attributable.
        body: The reply text.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    author: NotBlankStr
    author_agent_id: NotBlankStr
    body: NotBlankStr


@runtime_checkable
class PlanItemReplyService(Protocol):
    """Answers an operator's plan-item comment as the responsible role.

    Implementations are best-effort: :meth:`reply` always returns either a
    reply or ``None`` and never raises for a model/parse failure.
    """

    async def reply(
        self,
        *,
        plan: Plan,
        item: PlanItem,
        comment_body: str,
        active: tuple[AgentIdentity, ...],
    ) -> AgentReply | None:
        """Return a grounded reply to a plan-item comment, or ``None``.

        Args:
            plan: The plan the item belongs to (for objective context).
            item: The item the operator commented on.
            comment_body: The operator's comment, verbatim.
            active: The active agent roster, to resolve the item's owner.

        Returns:
            The attributed reply, or ``None`` when the call fails or yields
            no usable answer.
        """
        ...


class LlmPlanItemReplyService:
    """Answers a plan-item comment via one grounded completion call.

    Resolves the responder (the item's owner role if an active agent holds
    it, else the Chief of Staff) and makes one bounded, fenced completion
    over the item's own text, attributing the reply to the responder.

    Args:
        provider: Build-time completion provider bound to ``model``.
        model: Build-time reply model id, bound to ``provider``.
        temperature: Sampling temperature for the reply call.
        max_tokens: Token budget for one reply.
        timeout_seconds: Wall-clock cap for the reply call.
        cost_tracker: Optional cost tracker for the reply call.
        provider_registry: Registry used to resolve the live model ref's
            named provider; ``None`` pins dispatch to the build-time pair.
        config_resolver: Optional resolver for the live reply model.
    """

    _PURPOSE_ID: ClassVar[PromptPurposeId] = PromptPurposeId.PLAN_REVIEW_ITEM_REPLY

    @property
    def metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for this prompt class."""
        return pin_for(self._PURPOSE_ID)

    def __init__(  # noqa: PLR0913 -- DI seam: independently-wired knobs
        self,
        *,
        provider: CompletionProvider,
        model: NotBlankStr,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        cost_tracker: CostTrackerProtocol | None = None,
        provider_registry: ProviderRegistry | None = None,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds
        self._cost_tracker = cost_tracker
        self._provider_registry = provider_registry
        self._config_resolver = config_resolver

    async def reply(
        self,
        *,
        plan: Plan,
        item: PlanItem,
        comment_body: str,
        active: tuple[AgentIdentity, ...],
    ) -> AgentReply | None:
        """Return the responsible role's grounded reply, or ``None``.

        Returns:
            The attributed reply, or ``None`` when the call fails or yields
            no usable answer.
        """
        name, agent_id, role = self._resolve_responder(item, active)
        body = await self._request(
            plan=plan, item=item, comment_body=comment_body, role=role, name=name
        )
        if body is None:
            return None
        logger.info(
            PLAN_REVIEW_REPLY_ADDED,
            plan_id=str(plan.id),
            item_id=item.id,
            responder_id=agent_id,
        )
        return AgentReply(author=name, author_agent_id=agent_id, body=NotBlankStr(body))

    def _resolve_responder(
        self, item: PlanItem, active: tuple[AgentIdentity, ...]
    ) -> tuple[NotBlankStr, NotBlankStr, str]:
        """Pick who answers: the item's owner role, else the Chief of Staff.

        Returns:
            A ``(display_name, author_agent_id, role_label)`` triple; the
            Chief-of-Staff fallback carries a system id and a generic label.
        """
        if item.owner is not None:
            agent = resolve_agent_for_role(active, item.owner)
            if agent is not None:
                return (
                    NotBlankStr(agent.name),
                    NotBlankStr(str(agent.id)),
                    agent.role,
                )
        return _COS_NAME, _COS_AGENT_ID, "Chief of Staff"

    async def _resolve_live_dispatch(self) -> tuple[CompletionProvider, str]:
        """Resolve the live ``(provider, model)`` pair for one reply call.

        Re-reads ``coordination.plan_review_reply_model`` so an operator
        retargets it without a restart, resolving BOTH halves from the SAME
        ref. A live ref that is absent, provider-less, unregistered, or missing
        a model id falls back to the build-time pair.

        Returns:
            The provider driver and model id to dispatch this call on.
        """
        if self._config_resolver is None or self._provider_registry is None:
            return self._provider, self._model
        raw_ref = await resolve_str_with_fallback(
            resolver=self._config_resolver,
            namespace=SettingNamespace.COORDINATION,
            key="plan_review_reply_model",
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

    async def _resolve_live_generation(self) -> tuple[float, int, float]:
        """Resolve the live temperature, token budget, and timeout for a reply.

        Re-reads ``coordination.plan_review_reply_temperature`` /
        ``plan_review_reply_max_tokens`` / ``plan_review_reply_timeout_seconds``
        per call so an operator retunes them without a restart, matching the
        live model resolution; a missing resolver or setting falls back to the
        build-time value.

        Returns:
            The ``(temperature, max_tokens, timeout_seconds)`` to run this reply
            call with.
        """
        if self._config_resolver is None:
            return self._temperature, self._max_tokens, self._timeout_seconds
        temperature = await resolve_float_with_fallback(
            resolver=self._config_resolver,
            namespace=SettingNamespace.COORDINATION,
            key="plan_review_reply_temperature",
            fallback=self._temperature,
        )
        max_tokens = await resolve_int_with_fallback(
            resolver=self._config_resolver,
            namespace=SettingNamespace.COORDINATION,
            key="plan_review_reply_max_tokens",
            fallback=self._max_tokens,
        )
        timeout_seconds = await resolve_float_with_fallback(
            resolver=self._config_resolver,
            namespace=SettingNamespace.COORDINATION,
            key="plan_review_reply_timeout_seconds",
            fallback=self._timeout_seconds,
        )
        return temperature, max_tokens, timeout_seconds

    async def _request(
        self,
        *,
        plan: Plan,
        item: PlanItem,
        comment_body: str,
        role: str,
        name: NotBlankStr,
    ) -> str | None:
        """Run one reply call and return its text.

        Best-effort: a model hiccup or empty reply yields ``None`` (no reply)
        rather than propagating.

        Returns:
            The reply text, or ``None`` on a call failure or empty answer.
        """
        user = _REPLY_USER.format(
            objective=wrap_untrusted(TAG_TASK_DATA, plan.objective_title),
            item=wrap_untrusted(TAG_TASK_DATA, _render_item(item)),
            comment=wrap_untrusted(TAG_TASK_DATA, comment_body),
        )
        # The responder's role and name come from operator-controlled roster
        # data, so they are fenced as untrusted before entering the system
        # prompt: a crafted role/display name cannot inject instructions at
        # system priority.
        messages = [
            ChatMessage(
                role=MessageRole.SYSTEM,
                content=_REPLY_SYSTEM.format(
                    role=wrap_untrusted(TAG_TASK_DATA, role),
                    name=wrap_untrusted(TAG_TASK_DATA, name),
                ),
            ),
            ChatMessage(role=MessageRole.USER, content=user),
        ]
        temperature, max_tokens, timeout_seconds = await self._resolve_live_generation()
        completion_config = CompletionConfig(
            temperature=temperature, max_tokens=max_tokens
        )
        provider, model = await self._resolve_live_dispatch()
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=_REPLY_AGENT_ID,
                task_id=_REPLY_TASK_ID,
                purpose=self.metadata.prompt_class_id,
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await asyncio.wait_for(
                    provider.complete(messages, model, config=completion_config),
                    timeout=timeout_seconds,
                )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- an inline reply is a best-effort side
            # channel; a failed reply must never fail the operator's comment.
            reraise_critical(exc)
            logger.warning(
                PLAN_REVIEW_REPLY_FAILED,
                detail="reply_call_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                retry_after=getattr(exc, "retry_after", None),
            )
            return None
        body = (response.content or "").strip()
        if not body:
            logger.info(PLAN_REVIEW_REPLY_SKIPPED, detail="empty_reply")
            return None
        return body


def _render_item(item: PlanItem) -> str:
    """Render one plan item to reply-legible text.

    Returns:
        A plain-text rendering of the item (title, owner, kind, stakes,
        description, acceptance criteria, and any decision options) for the
        responder to ground its reply in.
    """
    owner = item.owner or "UNASSIGNED"
    lines = [
        f"{item.title} ({item.kind.value})",
        (
            f"owner: {owner} | stakes: {item.stakes.value}"
            f" | complexity: {item.estimated_complexity.value}"
        ),
        item.description,
    ]
    if item.acceptance_criteria:
        lines.append(f"done when: {'; '.join(item.acceptance_criteria)}")
    for option in item.options:
        mark = " (recommended)" if option.recommended else ""
        lines.append(f"option [{option.id}] {option.title}{mark}: {option.summary}")
    return "\n".join(lines)


def _resolve_reply_provider(
    provider_registry: ProviderRegistry, model: str
) -> tuple[CompletionProvider | None, str]:
    """Resolve the provider the reply model ref is bound to.

    The reply model is an explicit ``(provider, model)`` ref: a bare id is
    never auto-resolved, so a provider-less setting leaves the service unbuilt
    (replies simply do not run) rather than binding to an arbitrary gateway.

    Returns:
        A ``(driver, model_id)`` pair. ``driver`` is ``None`` when the ref
        names no provider or an unregistered one; ``model_id`` is always the
        ref's model id (possibly empty).
    """
    ref = parse_model_ref(model)
    if not ref.provider:
        logger.warning(
            PLAN_REVIEW_REPLY_FAILED,
            detail="reply_model_has_no_provider",
            model=ref.model_id,
        )
        return None, ref.model_id
    try:
        return provider_registry.get(ref.provider), ref.model_id
    except DriverNotRegisteredError:
        logger.warning(
            PLAN_REVIEW_REPLY_FAILED,
            detail="no_provider_for_reply_model",
            model=ref.model_id,
            provider=ref.provider,
        )
        return None, ref.model_id


def build_plan_item_reply_service(  # noqa: PLR0913 -- boot wiring deps
    *,
    reply_model: str,
    temperature: float,
    max_tokens: int,
    timeout_seconds: float,
    provider_registry: ProviderRegistry,
    cost_tracker: CostTrackerProtocol | None = None,
    config_resolver: ConfigResolver | None = None,
) -> PlanItemReplyService | None:
    """Build the plan-item reply service, or ``None`` when unbuildable.

    Built unconditionally of ``plan_review_reply_enabled`` (the controller
    gates it per comment on the live flag, so the instance must exist for the
    flag to flip on without a restart). Returns ``None`` only when no reply
    model is configured or no provider serves it, in which case comments post
    with no agent reply.

    Args:
        reply_model: The configured ``(provider, model)`` reply model ref.
        temperature: Sampling temperature for a reply.
        max_tokens: Token budget for one reply.
        timeout_seconds: Wall-clock cap for a reply call.
        provider_registry: Source of the reply provider.
        cost_tracker: Optional cost tracker for reply calls.
        config_resolver: Optional resolver for the live reply model.

    Returns:
        A service, or ``None`` when no model/provider is available.
    """
    if not reply_model:
        logger.info(PLAN_REVIEW_REPLY_FAILED, detail="reply_model_not_configured")
        return None
    provider, model_id = _resolve_reply_provider(provider_registry, reply_model)
    if provider is None:
        return None
    if not model_id:
        logger.warning(PLAN_REVIEW_REPLY_FAILED, detail="reply_model_has_no_model_id")
        return None
    return LlmPlanItemReplyService(
        provider=provider,
        model=NotBlankStr(model_id),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        cost_tracker=cost_tracker,
        provider_registry=provider_registry,
        config_resolver=config_resolver,
    )


__all__ = [
    "AgentReply",
    "LlmPlanItemReplyService",
    "PlanItemReplyService",
    "build_plan_item_reply_service",
]
