"""Unit tests for the pin probe runner.

Covers: the runner drives the pinned model + sampling config through the
provider, returns the provider output, is deterministic, and maps an
empty completion to an empty string.
"""

from typing import override

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.execution.turn import BehaviorTag
from synthorg.hr.evaluation.external_benchmark_models import EvalTestCase
from synthorg.hr.evaluation.pin_probe import (
    PIN_META_KEY,
    pin_metadata_payload,
    probe_input_data,
)
from synthorg.hr.evaluation.pin_probe_runner import PinProbeRunner
from synthorg.llm.model_pins import pin_for
from synthorg.llm.model_tier_policy import model_id_for_purpose
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)

pytestmark = pytest.mark.unit

_PURPOSE = PromptPurposeId.MEMORY_RERANK


def _case(purpose_id: PromptPurposeId = _PURPOSE) -> EvalTestCase:
    pin = pin_for(purpose_id)
    return EvalTestCase(
        id=str(purpose_id),
        behavior_tags=(BehaviorTag.VERIFICATION,),
        input_data=probe_input_data(purpose_id),
        expected_output="",
        metadata={PIN_META_KEY: pin_metadata_payload(pin)},
    )


class _RecordingStrategy:
    """Scripted strategy that records the model + config it is called with."""

    def __init__(self) -> None:
        self.model: str | None = None
        self.config: CompletionConfig | None = None
        self.messages: list[ChatMessage] = []

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        del tools
        self.messages = messages
        self.model = model
        self.config = config
        return CompletionResponse(
            content="OK",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=1, output_tokens=1, cost=0.0),
            model=model,
        )


async def test_runner_drives_pinned_model_and_config() -> None:
    strategy = _RecordingStrategy()
    runner = PinProbeRunner(provider=ScriptedDriver(strategy=strategy))

    output = await runner.run_case(_case())

    assert output == "OK"
    assert strategy.model == model_id_for_purpose(_PURPOSE)
    assert strategy.config is not None
    assert strategy.config.temperature == pytest.approx(0.0)
    assert strategy.config.top_p == pytest.approx(1.0)
    assert strategy.config.max_tokens == pin_for(_PURPOSE).max_tokens
    assert strategy.messages[0].content == probe_input_data(_PURPOSE)


async def test_runner_is_deterministic() -> None:
    runner = PinProbeRunner(provider=ScriptedDriver(provider_name="test-provider"))
    first = await runner.run_case(_case())
    second = await runner.run_case(_case())
    assert first == second


async def test_runner_maps_contentless_completion_to_empty_string() -> None:
    class _ToolOnlyStrategy(_RecordingStrategy):
        @override
        def next_response(
            self,
            messages: list[ChatMessage],
            model: str,
            tools: list[ToolDefinition] | None,
            config: CompletionConfig | None,
        ) -> CompletionResponse:
            del messages, tools, config
            return CompletionResponse(
                content=None,
                tool_calls=(ToolCall(id="t1", name="noop", arguments={}),),
                finish_reason=FinishReason.TOOL_USE,
                usage=TokenUsage(input_tokens=1, output_tokens=0, cost=0.0),
                model=model,
            )

    runner = PinProbeRunner(provider=ScriptedDriver(strategy=_ToolOnlyStrategy()))
    assert await runner.run_case(_case()) == ""
