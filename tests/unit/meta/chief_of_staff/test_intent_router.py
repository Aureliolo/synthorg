# module-kind: tests
"""Unit tests for unified turn-intent classification."""

import asyncio
from datetime import UTC, datetime
from typing import override

import pytest

from synthorg.communication.conversation.enums import ConversationRole
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.intent_models import (
    MODEL_ATTRIBUTED_REASONS,
    IntentOutcome,
    IntentRoutingReason,
    TurnIntent,
)
from synthorg.meta.chief_of_staff.intent_router import (
    LlmIntentClassifier,
    build_intent_classifier,
)
from synthorg.meta.chief_of_staff.models import ConversationTurn
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.model_ref import ModelRef, serialize_model_ref
from tests._shared import as_uuid, sid
from tests._shared.scripted_provider import ScriptedProvider, make_text_response

pytestmark = pytest.mark.unit

_START = datetime(2026, 5, 19, 9, 0, 0, tzinfo=UTC)


def _user_turn(text: str) -> tuple[ConversationTurn, ...]:
    return (
        ConversationTurn(
            id=as_uuid("turn-1"),
            conversation_id=sid("conv-1"),
            sequence=0,
            role=ConversationRole.USER,
            content=NotBlankStr(text),
            created_at=_START,
        ),
    )


def _intent_json(
    *, intent: str, confidence: float, named_targets: tuple[str, ...] = ()
) -> str:
    targets = ", ".join(f'"{t}"' for t in named_targets)
    return (
        f'{{"intent": "{intent}", "confidence": {confidence}, '
        f'"named_targets": [{targets}]}}'
    )


def _classifier(
    *,
    provider: ScriptedProvider,
    act_floor: float = 0.85,
    charter_floor: float = 0.8,
    configure_floor: float = 0.85,
    timeout_seconds: float = 120.0,
) -> LlmIntentClassifier:
    return LlmIntentClassifier(
        provider=provider,
        model=NotBlankStr("test-model-001"),
        act_confidence_floor=act_floor,
        charter_confidence_floor=charter_floor,
        configure_confidence_floor=configure_floor,
        temperature=0.0,
        max_tokens=200,
        timeout_seconds=timeout_seconds,
    )


class _HangingProvider(ScriptedProvider):
    """Provider whose ``complete`` never returns, to exercise the timeout."""

    def __init__(self) -> None:
        super().__init__(responses=[])

    @override
    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        del messages, model, tools, config
        await asyncio.Event().wait()
        raise AssertionError  # unreachable -- the wait never completes


def _scripted(text: str) -> ScriptedProvider:
    return ScriptedProvider(responses=[make_text_response(text)])


class TestLlmIntentClassifier:
    async def test_question_classifies_explain(self) -> None:
        provider = _scripted(_intent_json(intent="explain", confidence=0.9))
        outcome = await _classifier(provider=provider).classify(
            _user_turn("What is our runway?")
        )
        assert outcome.intent is TurnIntent.EXPLAIN
        assert outcome.reason is IntentRoutingReason.CLASSIFIED
        assert outcome.confidence == pytest.approx(0.9)

    async def test_work_request_classifies_propose(self) -> None:
        provider = _scripted(_intent_json(intent="propose", confidence=0.8))
        outcome = await _classifier(provider=provider).classify(
            _user_turn("Build me a landing page")
        )
        assert outcome.intent is TurnIntent.PROPOSE
        assert outcome.reason is IntentRoutingReason.CLASSIFIED

    async def test_confident_act_resolves_act(self) -> None:
        provider = _scripted(
            _intent_json(intent="act", confidence=0.95, named_targets=("CFO",))
        )
        outcome = await _classifier(provider=provider).classify(
            _user_turn("have the CFO send the invoice now")
        )
        assert outcome.intent is TurnIntent.ACT
        assert outcome.reason is IntentRoutingReason.CLASSIFIED

    async def test_low_confidence_act_degrades_to_explain(self) -> None:
        # Below the 0.85 act floor: an uncertain classifier must never act.
        provider = _scripted(_intent_json(intent="act", confidence=0.7))
        outcome = await _classifier(provider=provider).classify(
            _user_turn("maybe update the thing")
        )
        assert outcome.intent is TurnIntent.EXPLAIN
        assert outcome.reason is IntentRoutingReason.ACT_FLOOR_NOT_MET

    async def test_low_confidence_charter_degrades_to_explain(self) -> None:
        provider = _scripted(_intent_json(intent="charter", confidence=0.7))
        outcome = await _classifier(provider=provider).classify(
            _user_turn("something about a new company maybe")
        )
        assert outcome.intent is TurnIntent.EXPLAIN
        assert outcome.reason is IntentRoutingReason.CHARTER_FLOOR_NOT_MET

    async def test_confident_charter_resolves_charter(self) -> None:
        provider = _scripted(_intent_json(intent="charter", confidence=0.9))
        outcome = await _classifier(provider=provider).classify(
            _user_turn("Set up a new SaaS company charter")
        )
        assert outcome.intent is TurnIntent.CHARTER
        assert outcome.reason is IntentRoutingReason.CLASSIFIED

    async def test_group_convene_needs_two_targets(self) -> None:
        provider = _scripted(
            _intent_json(intent="group_convene", confidence=0.9, named_targets=("CFO",))
        )
        outcome = await _classifier(provider=provider).classify(
            _user_turn("get the CFO to look at this")
        )
        assert outcome.intent is TurnIntent.EXPLAIN
        assert outcome.reason is IntentRoutingReason.GROUP_TARGETS_MISSING

    async def test_group_convene_with_two_targets_resolves(self) -> None:
        provider = _scripted(
            _intent_json(
                intent="group_convene",
                confidence=0.9,
                named_targets=("CFO", "CTO"),
            )
        )
        outcome = await _classifier(provider=provider).classify(
            _user_turn("have the CFO and CTO discuss the budget")
        )
        assert outcome.intent is TurnIntent.GROUP_CONVENE
        assert outcome.reason is IntentRoutingReason.CLASSIFIED
        assert outcome.named_targets == (NotBlankStr("CFO"), NotBlankStr("CTO"))

    async def test_group_convene_with_duplicate_targets_degrades(self) -> None:
        # Two names that differ only in case are ONE voice, not a group, so the
        # convene degrades to a plain turn rather than talking to itself.
        provider = _scripted(
            _intent_json(
                intent="group_convene",
                confidence=0.9,
                named_targets=("CFO", "cfo"),
            )
        )
        outcome = await _classifier(provider=provider).classify(
            _user_turn("have the CFO and the cfo hash it out")
        )
        assert outcome.intent is TurnIntent.EXPLAIN
        assert outcome.reason is IntentRoutingReason.GROUP_TARGETS_MISSING

    async def test_confident_configure_resolves_configure(self) -> None:
        provider = _scripted(_intent_json(intent="configure", confidence=0.95))
        outcome = await _classifier(provider=provider).classify(
            _user_turn("Connect our GitHub organisation")
        )
        assert outcome.intent is TurnIntent.CONFIGURE
        assert outcome.reason is IntentRoutingReason.CLASSIFIED

    async def test_low_confidence_configure_degrades_to_explain(self) -> None:
        # Below the 0.85 configure floor: an uncertain classifier must never
        # drive the control plane.
        provider = _scripted(_intent_json(intent="configure", confidence=0.7))
        outcome = await _classifier(provider=provider).classify(
            _user_turn("maybe set up something")
        )
        assert outcome.intent is TurnIntent.EXPLAIN
        assert outcome.reason is IntentRoutingReason.CONFIGURE_FLOOR_NOT_MET

    async def test_malformed_json_degrades_to_explain(self) -> None:
        provider = _scripted("not json at all")
        outcome = await _classifier(provider=provider).classify(_user_turn("anything"))
        assert outcome.intent is TurnIntent.EXPLAIN
        assert outcome.reason is IntentRoutingReason.RESPONSE_INVALID

    async def test_invalid_intent_value_degrades_to_explain(self) -> None:
        provider = _scripted(_intent_json(intent="teleport", confidence=0.9))
        outcome = await _classifier(provider=provider).classify(_user_turn("anything"))
        assert outcome.intent is TurnIntent.EXPLAIN
        assert outcome.reason is IntentRoutingReason.RESPONSE_INVALID

    async def test_classifier_timeout_degrades_to_explain(self) -> None:
        classifier = _classifier(provider=_HangingProvider(), timeout_seconds=0.05)
        outcome = await classifier.classify(_user_turn("anything"))
        assert outcome.intent is TurnIntent.EXPLAIN
        assert outcome.reason is IntentRoutingReason.CLASSIFY_CALL_FAILED


class TestModelAttribution:
    """``IntentOutcome.model`` names the model that produced the verdict."""

    @pytest.mark.parametrize("reason", sorted(MODEL_ATTRIBUTED_REASONS))
    def test_a_dispatched_verdict_must_name_its_model(
        self, reason: IntentRoutingReason
    ) -> None:
        # Logging a routing decision with no model is what left the dogfood
        # unable to tell which model misrouted a turn.
        with pytest.raises(ValueError, match="model is required"):
            IntentOutcome(intent=TurnIntent.EXPLAIN, reason=reason)

    @pytest.mark.parametrize(
        "reason",
        sorted(set(IntentRoutingReason) - MODEL_ATTRIBUTED_REASONS),
    )
    def test_an_undispatched_outcome_must_not_name_one(
        self, reason: IntentRoutingReason
    ) -> None:
        # Naming a model against a decision it never made is worse than naming
        # none: it points the diagnosis at an innocent model.
        with pytest.raises(ValueError, match="model must be absent"):
            IntentOutcome(
                intent=TurnIntent.EXPLAIN,
                reason=reason,
                model=NotBlankStr("example-medium-001"),
            )


class TestBuildIntentClassifier:
    def test_unconfigured_model_returns_none(self) -> None:
        config = ChiefOfStaffConfig(turn_intent_model=None)
        assert (
            build_intent_classifier(
                config=config, provider_registry=ProviderRegistry(drivers={})
            )
            is None
        )

    def test_provider_less_ref_returns_none(self) -> None:
        # A bare model id with no provider must not auto-pick a gateway.
        config = ChiefOfStaffConfig(
            turn_intent_model=NotBlankStr(
                serialize_model_ref(ModelRef(provider="", model_id="example-small-001"))
            )
        )
        assert (
            build_intent_classifier(
                config=config, provider_registry=ProviderRegistry(drivers={})
            )
            is None
        )

    def test_unregistered_provider_returns_none(self) -> None:
        config = ChiefOfStaffConfig(
            turn_intent_model=NotBlankStr(
                serialize_model_ref(
                    ModelRef(provider="ghost", model_id="example-small-001")
                )
            )
        )
        assert (
            build_intent_classifier(
                config=config, provider_registry=ProviderRegistry(drivers={})
            )
            is None
        )
