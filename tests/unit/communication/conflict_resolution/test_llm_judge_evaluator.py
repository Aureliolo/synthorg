"""Unit tests for the LLM-backed conflict judge."""

import json
from datetime import UTC, datetime
from typing import override

import pytest

from synthorg.budget.tracker import CostTracker
from synthorg.communication.conflict_resolution.llm_judge_evaluator import (
    LlmJudgeEvaluator,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictPosition,
)
from synthorg.communication.enums import ConflictType
from synthorg.communication.errors import ConflictStrategyError
from synthorg.core.types import NotBlankStr
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.providers.cost_recording import (
    CostRecordingContext,
    current_cost_context,
)
from synthorg.providers.errors import ProviderError
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
)
from tests._shared.ids import as_uuid
from tests._shared.model_binding import model_ref_resolver, one_connection
from tests._shared.scripted_provider import ScriptedProvider, make_text_response

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 22, tzinfo=UTC)
_JUDGE = NotBlankStr("conflict_reviewer")


def _position(agent_id: str, *, position: str, role: str) -> ConflictPosition:
    return ConflictPosition(
        agent_id=agent_id,
        agent_department="Engineering",
        agent_role=role,
        position=position,
        reasoning=f"{agent_id} argues: {position}",
        timestamp=_NOW,
    )


def _conflict() -> Conflict:
    return Conflict(
        id=as_uuid("conflict-judge"),
        type=ConflictType.OTHER,
        subject="Adopt REST or gRPC for the internal API",
        positions=(
            _position("alice", position="REST is simpler", role="Software Architect"),
            _position("bob", position="gRPC is faster", role="Backend Developer"),
        ),
        detected_at=_NOW,
    )


def _verdict_payload(winning_agent_id: str, reasoning: str = "clear winner") -> str:
    return json.dumps({"winning_agent_id": winning_agent_id, "reasoning": reasoning})


class _CtxCapturingProvider(ScriptedProvider):
    """ScriptedProvider recording the cost-recording context per call."""

    def __init__(self, payload: str) -> None:
        super().__init__(response=make_text_response(payload))
        self.captured: CostRecordingContext | None = None

    @override
    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        self.captured = current_cost_context()
        return await super().complete(messages, model, tools=tools, config=config)


async def test_evaluate_parses_structured_response_to_judge_decision() -> None:
    provider = ScriptedProvider(response=make_text_response(_verdict_payload("alice")))
    evaluator = LlmJudgeEvaluator(
        connections=one_connection(provider), config_resolver=model_ref_resolver()
    )

    decision = await evaluator.evaluate(_conflict(), _JUDGE)

    assert decision.winning_agent_id == "alice"
    assert decision.reasoning == "clear winner"


async def test_evaluate_maps_ambiguous_token_to_sentinel() -> None:
    provider = ScriptedProvider(
        response=make_text_response(_verdict_payload("ambiguous", "genuine trade-off"))
    )
    evaluator = LlmJudgeEvaluator(
        connections=one_connection(provider), config_resolver=model_ref_resolver()
    )

    decision = await evaluator.evaluate(_conflict(), _JUDGE)

    assert decision.winning_agent_id == ""
    assert decision.reasoning == "genuine trade-off"


async def test_evaluate_maps_hallucinated_winner_to_sentinel() -> None:
    provider = ScriptedProvider(response=make_text_response(_verdict_payload("carol")))
    evaluator = LlmJudgeEvaluator(
        connections=one_connection(provider), config_resolver=model_ref_resolver()
    )

    decision = await evaluator.evaluate(_conflict(), _JUDGE)

    assert decision.winning_agent_id == ""


async def test_evaluate_fences_positions_and_lists_directive() -> None:
    provider = ScriptedProvider(response=make_text_response(_verdict_payload("alice")))
    evaluator = LlmJudgeEvaluator(
        connections=one_connection(provider), config_resolver=model_ref_resolver()
    )

    await evaluator.evaluate(_conflict(), _JUDGE)

    system, user = provider.received_messages[0]
    assert user.content is not None
    assert system.content is not None
    assert "<conflict-position>" in user.content
    assert "<task-data>" in user.content
    # The untrusted-content directive enumerates both fences in the system prompt.
    assert "<conflict-position>" in system.content
    # Trusted structural metadata stays outside the fence.
    assert "role: Software Architect" in user.content


async def test_evaluate_raises_conflict_strategy_error_on_malformed_json() -> None:
    provider = ScriptedProvider(response=make_text_response("not json at all"))
    evaluator = LlmJudgeEvaluator(
        connections=one_connection(provider), config_resolver=model_ref_resolver()
    )

    with pytest.raises(ConflictStrategyError):
        await evaluator.evaluate(_conflict(), _JUDGE)


async def test_evaluate_raises_conflict_strategy_error_on_schema_violation() -> None:
    # Valid JSON, but a blank winning_agent_id violates NotBlankStr.
    payload = json.dumps({"winning_agent_id": "", "reasoning": "x"})
    provider = ScriptedProvider(response=make_text_response(payload))
    evaluator = LlmJudgeEvaluator(
        connections=one_connection(provider), config_resolver=model_ref_resolver()
    )

    with pytest.raises(ConflictStrategyError):
        await evaluator.evaluate(_conflict(), _JUDGE)


async def test_evaluate_propagates_provider_error() -> None:
    provider = ScriptedProvider(error=ProviderError("provider unavailable"))
    evaluator = LlmJudgeEvaluator(
        connections=one_connection(provider), config_resolver=model_ref_resolver()
    )

    with pytest.raises(ProviderError):
        await evaluator.evaluate(_conflict(), _JUDGE)


async def test_metadata_property_returns_conflict_judge_pin() -> None:
    provider = ScriptedProvider(response=make_text_response(_verdict_payload("alice")))
    evaluator = LlmJudgeEvaluator(
        connections=one_connection(provider), config_resolver=model_ref_resolver()
    )

    assert evaluator.metadata.prompt_class_id is PromptPurposeId.CONFLICT_JUDGE


async def test_evaluate_opens_purpose_scope() -> None:
    provider = _CtxCapturingProvider(_verdict_payload("alice"))
    tracker = CostTracker()
    evaluator = LlmJudgeEvaluator(
        connections=one_connection(provider),
        cost_tracker=tracker,
        config_resolver=model_ref_resolver(),
    )

    await evaluator.evaluate(_conflict(), _JUDGE)
    await tracker.drain_pending_records()

    assert provider.captured is not None
    assert provider.captured.prompt_class_id is PromptPurposeId.CONFLICT_JUDGE
