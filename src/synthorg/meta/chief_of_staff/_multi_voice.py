# module-kind: service
"""Multi-voice chime-ins for the unified conversational surface.

After the Chief of Staff answers a question, a specialist on the roster may
add a short, attributed chime-in from their own role, so the operator sees the
*organisation* answering rather than one synthesised voice. It is deliberately
selective: silence is the default, and a specialist speaks only when its role
adds a distinct, grounded perspective above a real value bar.

Best-effort by construction: a chime-in never *fails* the answer, and never
delays it by more than a bounded ``multi_voice_timeout_seconds`` on the buffered
path (the streaming path delivers it after the answer, off the critical path).
Any classifier error, invalid reply, or below-floor candidate yields no extra
voice, so the operator still gets the plain answer. Mirrors
:class:`~synthorg.meta.chief_of_staff.intent_router.LlmIntentClassifier`'s
provider-binding and best-effort discipline.
"""

import asyncio
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.meta.chief_of_staff._role_resolution import resolve_agent_for_role
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.prompts import (
    TURN_MULTI_VOICE_SYSTEM,
    TURN_MULTI_VOICE_USER,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_MULTI_VOICE_ADDED,
    COS_MULTI_VOICE_FAILED,
    COS_MULTI_VOICE_SKIPPED,
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

_VOICE_AGENT_ID: NotBlankStr = NotBlankStr("system")
_VOICE_TASK_ID: NotBlankStr = NotBlankStr("system:cos:multi_voice")


class ChimeIn(BaseModel):
    """One specialist's attributed chime-in on an answer.

    Attributes:
        role: The specialist's role (resolved to an active role-holder).
        name: The display name of the agent that holds the role.
        content: The short, role-voiced perspective added to the answer.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    role: NotBlankStr
    name: NotBlankStr
    content: NotBlankStr


class _VoiceCandidate(BaseModel):
    """A raw candidate chime-in from the model, before floor + resolution."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    role: NotBlankStr
    content: NotBlankStr
    confidence: float = Field(ge=0.0, le=1.0)


class _MultiVoiceResponse(BaseModel):
    """The structured output of one multi-voice model turn."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    voices: tuple[_VoiceCandidate, ...] = ()


@runtime_checkable
class MultiVoiceRouter(Protocol):
    """Picks specialists to add a grounded chime-in to an answer.

    Implementations are best-effort: :meth:`chime` always returns a
    (possibly empty) tuple and never raises for a model/parse failure.
    """

    async def chime(
        self,
        *,
        question: str,
        answer: str,
        active: tuple[AgentIdentity, ...],
    ) -> tuple[ChimeIn, ...]:
        """Return 0..N attributed chime-ins for an answered question.

        Args:
            question: The operator's question, verbatim.
            answer: The answer the Chief of Staff already gave.
            active: The active agent roster to draw specialists from.

        Returns:
            The resolved chime-ins, strongest-first; empty when no
            specialist clears the value bar or the roster is empty.
        """
        ...


class LlmMultiVoiceRouter:
    """Adds specialist chime-ins via one selective model call.

    Asks the model which roster specialists would add a distinct, grounded
    perspective, then keeps only those above the confidence floor whose role
    resolves to an active agent, capped at ``max_speakers`` and deduplicated
    by role.

    Args:
        provider: Build-time completion provider bound to ``model``.
        model: Build-time chime-in model id, bound to ``provider``.
        confidence_floor: Minimum confidence a candidate must clear.
        max_speakers: Maximum chime-ins returned for one answer.
        temperature: Sampling temperature for the chime-in call.
        max_tokens: Token budget for one chime-in reply.
        timeout_seconds: Wall-clock cap for the chime-in call.
        cost_tracker: Optional cost tracker for the chime-in call.
        provider_registry: Registry used to resolve the live model ref's
            named provider; ``None`` pins dispatch to the build-time pair.
        config_resolver: Optional resolver for the live ``multi_voice_model``.
    """

    _PURPOSE_ID: ClassVar[PromptPurposeId] = PromptPurposeId.COS_MULTI_VOICE

    @property
    def metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for this prompt class."""
        return pin_for(self._PURPOSE_ID)

    def __init__(  # noqa: PLR0913 -- DI seam: independently-wired knobs
        self,
        *,
        provider: CompletionProvider,
        model: NotBlankStr,
        confidence_floor: float,
        max_speakers: int,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        cost_tracker: CostTrackerProtocol | None = None,
        provider_registry: ProviderRegistry | None = None,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._confidence_floor = confidence_floor
        self._max_speakers = max_speakers
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds
        self._cost_tracker = cost_tracker
        self._provider_registry = provider_registry
        self._config_resolver = config_resolver

    async def _resolve_live_dispatch(self) -> tuple[CompletionProvider, str]:
        """Resolve the live ``(provider, model)`` pair for one chime call.

        Re-reads ``chief_of_staff.multi_voice_model`` so an operator retargets
        it without a restart, resolving BOTH halves from the SAME ref. A live
        ref that is absent, provider-less, unregistered, or missing a model id
        falls back to the build-time pair.

        Returns:
            The provider driver and model id to dispatch this call on.
        """
        if self._config_resolver is None or self._provider_registry is None:
            return self._provider, self._model
        raw_ref = await resolve_str_with_fallback(
            resolver=self._config_resolver,
            namespace=SettingNamespace.CHIEF_OF_STAFF,
            key="multi_voice_model",
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

    async def chime(
        self,
        *,
        question: str,
        answer: str,
        active: tuple[AgentIdentity, ...],
    ) -> tuple[ChimeIn, ...]:
        """Return the resolved chime-ins for an answered question.

        Returns:
            The chime-ins, strongest-first; empty when the roster is empty,
            the call fails, or no candidate clears the floor.
        """
        roster = _senior_per_role(active)
        if not roster:
            return ()
        response = await self._request(question=question, answer=answer, roster=roster)
        if response is None:
            return ()
        return self._resolve(response, active)

    def _resolve(
        self,
        response: _MultiVoiceResponse,
        active: tuple[AgentIdentity, ...],
    ) -> tuple[ChimeIn, ...]:
        """Filter, resolve, dedupe, and cap the raw candidates.

        Returns:
            Chime-ins for candidates above the floor whose role resolves to
            an active agent, one per role, strongest-first, capped at
            ``max_speakers``.
        """
        ranked = sorted(response.voices, key=lambda v: v.confidence, reverse=True)
        chimes: list[ChimeIn] = []
        used_roles: set[str] = set()
        for candidate in ranked:
            if candidate.confidence < self._confidence_floor:
                continue
            key = candidate.role.casefold()
            if key in used_roles:
                continue
            agent = resolve_agent_for_role(active, candidate.role)
            if agent is None:
                logger.info(COS_MULTI_VOICE_SKIPPED, detail="role_unresolved")
                continue
            used_roles.add(key)
            chimes.append(
                ChimeIn(
                    role=NotBlankStr(agent.role),
                    name=NotBlankStr(agent.name),
                    content=candidate.content,
                )
            )
            if len(chimes) >= self._max_speakers:
                break
        if chimes:
            logger.info(COS_MULTI_VOICE_ADDED, count=len(chimes))
        else:
            # Every candidate fell below the floor or failed to resolve; record a
            # skip so the added-event count is not inflated by empty rounds.
            logger.info(COS_MULTI_VOICE_SKIPPED, detail="no_candidate_selected")
        return tuple(chimes)

    async def _request(
        self, *, question: str, answer: str, roster: tuple[AgentIdentity, ...]
    ) -> _MultiVoiceResponse | None:
        """Run one chime-in call and parse its structured output.

        Best-effort: a model hiccup or invalid reply yields ``None`` (no
        chime-in) rather than propagating.

        Returns:
            The parsed response, or ``None`` on a call failure or invalid
            reply.
        """
        # Roster roles/names are operator-controlled data; fence them like the
        # question and answer so a crafted role or display name cannot inject
        # instructions into the chime-in prompt.
        roster_lines = "\n".join(f"- {a.role} -- {a.name}" for a in roster)
        user = TURN_MULTI_VOICE_USER.format(
            roster=wrap_untrusted(TAG_TASK_DATA, roster_lines),
            question=wrap_untrusted(TAG_TASK_DATA, question),
            answer=wrap_untrusted(TAG_TASK_DATA, answer),
        )
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=TURN_MULTI_VOICE_SYSTEM),
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
                agent_id=_VOICE_AGENT_ID,
                task_id=_VOICE_TASK_ID,
                purpose=self.metadata.prompt_class_id,
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await asyncio.wait_for(
                    provider.complete(messages, model, config=completion_config),
                    timeout=self._timeout_seconds,
                )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort enrichment; a chime-in
            # failure must never fail or block the answer it decorates.
            reraise_critical(exc)
            logger.warning(
                COS_MULTI_VOICE_FAILED,
                detail="chime_call_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                retry_after=getattr(exc, "retry_after", None),
            )
            return None
        raw = (response.content or "").strip()
        parsed = extract_json_from_llm_response(
            raw,
            logger_callback=lambda detail: logger.warning(
                COS_MULTI_VOICE_FAILED, detail=detail
            ),
        )
        if parsed is None:
            return None
        try:
            return _MultiVoiceResponse.model_validate(parsed)
        except ValidationError as exc:
            logger.warning(
                COS_MULTI_VOICE_FAILED,
                detail="schema_validation_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None


def _senior_per_role(
    active: tuple[AgentIdentity, ...],
) -> tuple[AgentIdentity, ...]:
    """Collapse the roster to one deterministic holder of each distinct role.

    The chime prompt lists one candidate per role, so an org with several
    agents sharing a role does not offer the model duplicate rows. Holders of
    a role share its authority, so the alphabetically-first name is the
    tiebreak, matching how ``resolve_agent_for_role`` later attributes it.

    Returns:
        One :class:`AgentIdentity` per distinct role (case-insensitive),
        ordered by name.
    """
    by_role: dict[str, AgentIdentity] = {}
    for agent in active:
        key = agent.role.casefold()
        held = by_role.get(key)
        # Holders of one role share its authority, so seniority never
        # separates them; the name-ascending tiebreak keeps this pick
        # deterministic and aligned with resolve_agent_for_role's attribution.
        if held is None or agent.name < held.name:
            by_role[key] = agent
    return tuple(
        sorted(by_role.values(), key=lambda a: a.name),
    )


def _resolve_voice_provider(
    provider_registry: ProviderRegistry, model: str
) -> tuple[CompletionProvider | None, str]:
    """Resolve the provider the chime-in model ref is bound to.

    The chime-in model is an explicit ``(provider, model)`` ref: a bare id is
    never auto-resolved, so a provider-less setting leaves the router unbuilt
    (chime-ins simply do not run) rather than binding to an arbitrary gateway.

    Returns:
        A ``(driver, model_id)`` pair. ``driver`` is ``None`` when the ref
        names no provider or an unregistered one; ``model_id`` is always the
        ref's model id (possibly empty).
    """
    ref = parse_model_ref(model)
    if not ref.provider:
        logger.warning(
            COS_MULTI_VOICE_FAILED,
            detail="multi_voice_model_has_no_provider",
            model=ref.model_id,
        )
        return None, ref.model_id
    try:
        return provider_registry.get(ref.provider), ref.model_id
    except DriverNotRegisteredError:
        logger.warning(
            COS_MULTI_VOICE_FAILED,
            detail="no_provider_for_multi_voice_model",
            model=ref.model_id,
            provider=ref.provider,
        )
        return None, ref.model_id


def build_multi_voice_router(
    *,
    config: ChiefOfStaffConfig,
    provider_registry: ProviderRegistry,
    cost_tracker: CostTrackerProtocol | None = None,
    config_resolver: ConfigResolver | None = None,
) -> MultiVoiceRouter | None:
    """Build the multi-voice router, or ``None`` when unbuildable.

    Built unconditionally of ``multi_voice_enabled`` (the dispatcher gates it
    per turn on the live flag, so the instance must exist for the flag to flip
    on without a restart). Returns ``None`` only when no chime-in model is
    configured or no provider serves it, in which case turns simply carry no
    chime-ins.

    Args:
        config: Chief of Staff configuration.
        provider_registry: Source of the chime-in provider.
        cost_tracker: Optional cost tracker for chime-in calls.
        config_resolver: Optional resolver for the live ``multi_voice_model``.

    Returns:
        A router, or ``None`` when no model/provider is available.
    """
    model = config.multi_voice_model
    if not model:
        logger.info(COS_MULTI_VOICE_FAILED, detail="multi_voice_model_not_configured")
        return None
    provider, model_id = _resolve_voice_provider(provider_registry, model)
    if provider is None:
        return None
    if not model_id:
        logger.warning(
            COS_MULTI_VOICE_FAILED, detail="multi_voice_model_has_no_model_id"
        )
        return None
    return LlmMultiVoiceRouter(
        provider=provider,
        model=NotBlankStr(model_id),
        confidence_floor=config.multi_voice_confidence_floor,
        max_speakers=config.multi_voice_max_speakers,
        temperature=config.multi_voice_temperature,
        max_tokens=config.multi_voice_max_tokens,
        timeout_seconds=config.multi_voice_timeout_seconds,
        cost_tracker=cost_tracker,
        provider_registry=provider_registry,
        config_resolver=config_resolver,
    )


__all__ = [
    "ChimeIn",
    "LlmMultiVoiceRouter",
    "MultiVoiceRouter",
    "build_multi_voice_router",
]
