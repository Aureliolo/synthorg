# module-kind: service
"""Turn-intent classification for the unified conversational surface.

Sits one level above concern routing. Concern routing (``routing.py``)
decides *who* answers a turn within an already-known capability; the intent
classifier here decides *which capability* the turn wants at all: answer a
question (``EXPLAIN``), request work (``PROPOSE``), act via a tool
(``ACT``), convene a group (``GROUP_CONVENE``), or start a charter interview
(``CHARTER``). The two compose: intent picks the capability, then the
existing :class:`~synthorg.meta.chief_of_staff.routing.RoleRouter` picks the
role voice within EXPLAIN/PROPOSE.

Classification is best-effort with a hard safety bias: any uncertainty
degrades toward ``EXPLAIN`` (a read), never toward ``ACT`` (a write) or
``CHARTER`` (an expensive multi-turn interview). ``ACT`` and ``CHARTER`` are
only returned above their own, stricter confidence floors; a malformed or
failed classification falls back to ``EXPLAIN``, mirroring
:class:`~synthorg.meta.chief_of_staff.routing.LlmConcernRouter`'s
best-effort discipline.
"""

import asyncio
from enum import StrEnum
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.meta.chief_of_staff.prompts import (
    TURN_INTENT_SYSTEM,
    TURN_INTENT_USER,
)
from synthorg.meta.chief_of_staff.transcript import render_turns_transcript
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_INTENT_CLASSIFIED,
    COS_INTENT_FALLBACK,
    COS_INTENT_RESPONSE_INVALID,
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

_INTENT_AGENT_ID: NotBlankStr = NotBlankStr("system")
_INTENT_TASK_ID: NotBlankStr = NotBlankStr("system:cos:turn_intent")
# A convened group needs at least two named participants to be a group at
# all; a "group" request naming fewer is treated as a plain turn.
_MIN_GROUP_TARGETS: int = 2


class TurnIntent(StrEnum):
    """Which org capability a single operator turn is asking for.

    Attributes:
        EXPLAIN: Answer a question about the org (read-only). The default
            and the safe fallback for any uncertain classification.
        PROPOSE: Turn a work request into a plan for holistic review.
        ACT: Perform a concrete system action now, via a tool, under the
            acting agent's trust level. Gated behind a stricter floor.
        GROUP_CONVENE: Convene several named agents in a group discussion.
        CHARTER: Interview the operator to draft a company charter.
    """

    EXPLAIN = "explain"
    PROPOSE = "propose"
    ACT = "act"
    GROUP_CONVENE = "group_convene"
    CHARTER = "charter"


class IntentRoutingReason(StrEnum):
    """Why a turn resolved to the intent it did.

    Surfaced on the turn result so a human can see whether the intent was
    classified, forced by an explicit override, fixed by the conversation's
    kind, or degraded to ``EXPLAIN`` because a stricter gate was not met.

    Attributes:
        CLASSIFIED: The classifier's pick was taken as-is.
        EXPLICIT_OVERRIDE: The caller supplied an explicit intent override.
        CONVERSATION_KIND_FIXED: An in-flight group/charter conversation
            dispatches to its owning capability without re-classification.
        NO_INTENT_CLASSIFIER: No classifier is wired; defaulted to EXPLAIN.
        ACT_FLOOR_NOT_MET: A confident-enough ACT was not reached; degraded
            to EXPLAIN.
        CHARTER_FLOOR_NOT_MET: A confident-enough CHARTER was not reached;
            degraded to EXPLAIN.
        GROUP_TARGETS_MISSING: A group was requested without enough named
            participants; degraded to EXPLAIN.
        CLASSIFY_CALL_FAILED: The classifier call errored or timed out;
            defaulted to EXPLAIN.
        RESPONSE_INVALID: The classifier reply failed to parse/validate;
            defaulted to EXPLAIN.
    """

    CLASSIFIED = "classified"
    EXPLICIT_OVERRIDE = "explicit_override"
    CONVERSATION_KIND_FIXED = "conversation_kind_fixed"
    NO_INTENT_CLASSIFIER = "no_intent_classifier"
    ACT_FLOOR_NOT_MET = "act_floor_not_met"
    CHARTER_FLOOR_NOT_MET = "charter_floor_not_met"
    GROUP_TARGETS_MISSING = "group_targets_missing"
    CLASSIFY_CALL_FAILED = "classify_call_failed"
    RESPONSE_INVALID = "response_invalid"


class IntentClassification(BaseModel):
    """Structured output of one intent-classification model turn.

    Attributes:
        intent: The capability the classifier picked.
        confidence: Classifier confidence (0-1) in the pick.
        named_targets: Roles/names the operator explicitly addressed, as
            the classifier read them; empty when none.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    intent: TurnIntent
    confidence: float = Field(ge=0.0, le=1.0)
    named_targets: tuple[NotBlankStr, ...] = ()


class IntentOutcome(BaseModel):
    """The resolved intent for a turn, plus why it landed.

    Attributes:
        intent: The capability the turn dispatches to.
        reason: Why this intent was chosen (classified, overridden, fixed
            by conversation kind, or degraded).
        confidence: Classifier confidence (0-1) when a classification ran;
            ``None`` for an override / fixed-kind / no-classifier outcome.
        named_targets: Roles/names surfaced by the classifier for a group
            convene; empty otherwise.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    intent: TurnIntent
    reason: IntentRoutingReason
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    named_targets: tuple[NotBlankStr, ...] = ()


@runtime_checkable
class IntentClassifier(Protocol):
    """Classifies one operator turn to a :class:`TurnIntent`.

    Implementations are best-effort: :meth:`classify` always returns an
    :class:`IntentOutcome`. Any uncertainty (classifier error, invalid
    reply, a below-floor ACT/CHARTER, a group without enough targets)
    yields ``EXPLAIN`` with the reason it landed there.
    """

    async def classify(self, history: tuple[ConversationTurn, ...]) -> IntentOutcome:
        """Classify the latest human turn to a capability intent.

        Args:
            history: Conversation turns oldest-first, ending with the human
                turn to classify.

        Returns:
            The resolved :class:`IntentOutcome`.
        """
        ...


class LlmIntentClassifier:
    """Classifies a turn's intent via a deterministic classifier call.

    Asks the classifier model which capability the latest human message
    wants, then applies the safety-biased floors: ACT and CHARTER need
    their own, stricter confidence; a group convene needs at least two
    named targets; every other uncertainty degrades to EXPLAIN.

    Args:
        provider: Build-time completion provider bound to ``model`` (the
            fallback dispatch when no live ref resolves).
        model: Build-time classifier model id, bound to ``provider``.
        act_confidence_floor: Minimum confidence to resolve ACT.
        charter_confidence_floor: Minimum confidence to resolve CHARTER.
        temperature: Classifier sampling temperature.
        max_tokens: Token budget for one classification reply.
        timeout_seconds: Wall-clock cap for the classification call.
        cost_tracker: Optional cost tracker for the classification call.
        provider_registry: Registry used to resolve the live model ref's
            named provider; ``None`` pins dispatch to the build-time pair.
        config_resolver: Optional resolver for the live ``turn_intent_model``.
    """

    _PURPOSE_ID: ClassVar[PromptPurposeId] = PromptPurposeId.COS_TURN_INTENT

    @property
    def metadata(self) -> ModelPinMetadata:
        """Pinned model + sampling for this prompt class."""
        return pin_for(self._PURPOSE_ID)

    def __init__(  # noqa: PLR0913 -- DI seam: independently-wired knobs
        self,
        *,
        provider: CompletionProvider,
        model: NotBlankStr,
        act_confidence_floor: float,
        charter_confidence_floor: float,
        temperature: float,
        max_tokens: int,
        timeout_seconds: float,
        cost_tracker: CostTrackerProtocol | None = None,
        provider_registry: ProviderRegistry | None = None,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._act_confidence_floor = act_confidence_floor
        self._charter_confidence_floor = charter_confidence_floor
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds
        self._cost_tracker = cost_tracker
        self._provider_registry = provider_registry
        self._config_resolver = config_resolver

    async def _resolve_live_dispatch(self) -> tuple[CompletionProvider, str]:
        """Resolve the live ``(provider, model)`` pair for one classify call.

        Re-reads ``chief_of_staff.turn_intent_model`` so an operator retargets
        it without a restart, resolving BOTH halves from the SAME ref: a newly
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
            key="turn_intent_model",
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

    async def classify(self, history: tuple[ConversationTurn, ...]) -> IntentOutcome:
        """Classify the latest human turn and apply the safety floors.

        Returns:
            The resolved outcome: the classifier's pick when it clears its
            floor, else ``EXPLAIN`` with the degrade reason.
        """
        classification = await self._classify(history)
        if isinstance(classification, IntentRoutingReason):
            return IntentOutcome(intent=TurnIntent.EXPLAIN, reason=classification)
        return self._apply_floors(classification)

    def _apply_floors(self, classification: IntentClassification) -> IntentOutcome:
        """Degrade a raw classification that does not clear its gate.

        Returns:
            The classified outcome, or an ``EXPLAIN`` outcome carrying the
            reason the stricter intent was not reached.
        """
        intent = classification.intent
        confidence = classification.confidence
        degrade: IntentRoutingReason | None = None
        if intent is TurnIntent.ACT and confidence < self._act_confidence_floor:
            degrade = IntentRoutingReason.ACT_FLOOR_NOT_MET
        elif (
            intent is TurnIntent.CHARTER and confidence < self._charter_confidence_floor
        ):
            degrade = IntentRoutingReason.CHARTER_FLOOR_NOT_MET
        elif (
            intent is TurnIntent.GROUP_CONVENE
            and len(classification.named_targets) < _MIN_GROUP_TARGETS
        ):
            degrade = IntentRoutingReason.GROUP_TARGETS_MISSING
        if degrade is not None:
            logger.info(
                COS_INTENT_FALLBACK,
                detail=degrade.value,
                intent=intent.value,
                confidence=confidence,
            )
            return IntentOutcome(
                intent=TurnIntent.EXPLAIN,
                reason=degrade,
                confidence=confidence,
            )
        logger.info(
            COS_INTENT_CLASSIFIED,
            intent=intent.value,
            confidence=confidence,
        )
        return IntentOutcome(
            intent=intent,
            reason=IntentRoutingReason.CLASSIFIED,
            confidence=confidence,
            named_targets=classification.named_targets,
        )

    async def _classify(
        self, history: tuple[ConversationTurn, ...]
    ) -> IntentClassification | IntentRoutingReason:
        """Run one classification call and parse its structured output.

        Classification is best-effort, so a classifier hiccup degrades to
        EXPLAIN rather than blocking the turn; the specific failure is
        returned as an :class:`IntentRoutingReason` so the caller can
        surface why.

        Returns:
            The parsed classification, or the fallback reason on a call
            failure (``CLASSIFY_CALL_FAILED``) or invalid response
            (``RESPONSE_INVALID``).
        """
        user = TURN_INTENT_USER.format(
            conversation_history=wrap_untrusted(
                TAG_TASK_DATA, render_turns_transcript(history)
            ),
        )
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=TURN_INTENT_SYSTEM),
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
                agent_id=_INTENT_AGENT_ID,
                task_id=_INTENT_TASK_ID,
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
            # Best-effort: any classifier failure degrades to EXPLAIN rather
            # than propagating. Preserve the provider-error classification
            # and any backoff hint so a repeated rate-limit driving the
            # fallback stays diagnosable.
            logger.warning(
                COS_INTENT_FALLBACK,
                detail="classify_call_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                retry_after=getattr(exc, "retry_after", None),
            )
            return IntentRoutingReason.CLASSIFY_CALL_FAILED
        raw = (response.content or "").strip()
        parsed = extract_json_from_llm_response(
            raw,
            logger_callback=lambda detail: logger.warning(
                COS_INTENT_RESPONSE_INVALID, detail=detail
            ),
        )
        if parsed is None:
            return IntentRoutingReason.RESPONSE_INVALID
        try:
            return IntentClassification.model_validate(parsed)
        except ValidationError as exc:
            logger.warning(
                COS_INTENT_RESPONSE_INVALID,
                detail="schema_validation_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return IntentRoutingReason.RESPONSE_INVALID


def _resolve_intent_provider(
    provider_registry: ProviderRegistry, model: str
) -> tuple[CompletionProvider | None, str]:
    """Resolve the provider the intent-classifier model ref is bound to.

    The intent model is an explicit ``(provider, model)`` ref: a bare id is
    never auto-resolved against "whichever provider serves it", so a
    provider-less setting leaves the classifier unbuilt (the turn degrades to
    EXPLAIN) rather than binding to an arbitrary gateway. The ref is parsed
    once here and its ``model_id`` returned so the caller need not re-parse.

    Returns:
        A ``(driver, model_id)`` pair. ``driver`` is ``None`` when the ref
        names no provider or an unregistered one; ``model_id`` is always the
        ref's model id (possibly empty) for the caller to validate.
    """
    ref = parse_model_ref(model)
    if not ref.provider:
        logger.warning(
            COS_INTENT_FALLBACK,
            detail="intent_model_has_no_provider",
            model=ref.model_id,
        )
        return None, ref.model_id
    try:
        return provider_registry.get(ref.provider), ref.model_id
    except DriverNotRegisteredError:
        logger.warning(
            COS_INTENT_FALLBACK,
            detail="no_provider_for_intent_model",
            model=ref.model_id,
            provider=ref.provider,
        )
        return None, ref.model_id


def build_intent_classifier(
    *,
    config: ChiefOfStaffConfig,
    provider_registry: ProviderRegistry,
    cost_tracker: CostTrackerProtocol | None = None,
    config_resolver: ConfigResolver | None = None,
) -> IntentClassifier | None:
    """Build the intent classifier, or ``None`` when unbuildable.

    The classifier is built unconditionally of ``turn_router_enabled`` (the
    endpoint gates the surface per request on the live flag, so the instance
    must exist for the flag to flip on without a restart). Returns ``None``
    only when no classifier model is configured or no provider serves it, in
    which case the orchestrator defaults every turn to EXPLAIN.

    Args:
        config: Chief of Staff configuration.
        provider_registry: Source of the classifier provider.
        cost_tracker: Optional cost tracker for classification calls.
        config_resolver: Optional resolver for the live ``turn_intent_model``.

    Returns:
        A classifier, or ``None`` when no model/provider is available.
    """
    intent_model = config.turn_intent_model
    if not intent_model:
        logger.info(COS_INTENT_FALLBACK, detail="turn_intent_model_not_configured")
        return None
    provider, model_id = _resolve_intent_provider(provider_registry, intent_model)
    if provider is None:
        return None
    if not model_id:
        logger.warning(COS_INTENT_FALLBACK, detail="intent_model_has_no_model_id")
        return None
    return LlmIntentClassifier(
        provider=provider,
        model=NotBlankStr(model_id),
        act_confidence_floor=config.act_intent_confidence_floor,
        charter_confidence_floor=config.charter_intent_confidence_floor,
        temperature=config.turn_intent_temperature,
        max_tokens=config.turn_intent_max_tokens,
        timeout_seconds=config.agent_call_timeout_seconds,
        cost_tracker=cost_tracker,
        provider_registry=provider_registry,
        config_resolver=config_resolver,
    )


__all__ = [
    "IntentClassification",
    "IntentClassifier",
    "IntentOutcome",
    "IntentRoutingReason",
    "LlmIntentClassifier",
    "TurnIntent",
    "build_intent_classifier",
]
