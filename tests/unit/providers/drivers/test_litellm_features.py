"""Tests for capability-gated request features on the LiteLLM driver."""

import pytest

from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.types import NotBlankStr
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.litellm_features import (
    apply_capability_gated_features,
)
from synthorg.providers.drivers.litellm_kwargs import _AcompletionKwargs
from synthorg.providers.models import CompletionConfig

pytestmark = pytest.mark.unit


def _model_config(model_id: str) -> ProviderModelConfig:
    return ProviderModelConfig(id=NotBlankStr(model_id))


def _caps(*, supports_reasoning: bool) -> ModelCapabilities:
    return ModelCapabilities(
        model_id="example-large-001",
        provider="example-provider",
        max_context_tokens=200_000,
        max_output_tokens=8192,
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        supports_reasoning=supports_reasoning,
    )


def _kwargs(model_id: str) -> _AcompletionKwargs:
    return {
        "model": f"openai/{model_id}",
        "messages": [{"role": "user", "content": "hello"}],
        "reasoning_effort": "high",
    }


def _apply(
    model_id: str,
    *,
    supports_reasoning: bool,
    routing_key: str,
) -> _AcompletionKwargs:
    return apply_capability_gated_features(
        _kwargs(model_id),
        _model_config(model_id),
        CompletionConfig(reasoning_effort=ReasoningEffort.HIGH),
        capabilities_provider=lambda _: _caps(supports_reasoning=supports_reasoning),
        provider_name="example-provider",
        routing_key=routing_key,
    )


class TestReasoningEffortGate:
    def test_dropped_when_the_model_cannot_reason(self) -> None:
        result = _apply(
            "example-large-001", supports_reasoning=False, routing_key="openai"
        )

        assert "reasoning_effort" not in result

    def test_dropped_when_the_route_will_not_carry_the_parameter(self) -> None:
        """Our metadata claiming reasoning is not enough to send the parameter.

        An OpenAI-compatible endpoint validates request parameters against
        LiteLLM's own view of the model. A model absent from that view rejects
        ``reasoning_effort`` outright with a non-retryable error, which fails
        the task on turn one, so the route's answer overrides ours.
        """
        result = _apply(
            "definitely-not-a-mapped-model",
            supports_reasoning=True,
            routing_key="openai",
        )

        assert "reasoning_effort" not in result

    def test_kept_when_both_the_model_and_the_route_support_it(self) -> None:
        result = _apply("gpt-5", supports_reasoning=True, routing_key="openai")

        assert result["reasoning_effort"] == "high"

    def test_kept_for_a_route_litellm_does_not_enumerate(self) -> None:
        """A route with no published parameter list is not evidence of refusal.

        Ollama is served from our own ``/api/show`` probe rather than LiteLLM's
        static database, so an empty answer there means "unknown", and
        withholding reasoning from every local model would be a regression.
        """
        result = _apply(
            "example-large-001", supports_reasoning=True, routing_key="ollama"
        )

        assert result["reasoning_effort"] == "high"
