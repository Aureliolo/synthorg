"""Tests for capability-gated request features on the LiteLLM driver."""

from collections.abc import Sequence

import litellm
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

_MODEL_ID = "example-expert-001"

#: The gate treats the routing key as opaque: it forwards it to LiteLLM's
#: lookup and branches only on the answer, which every test here pins. So the
#: two keys carry no vendor meaning and name only the case under test.
_HOSTED_ROUTE = "example-hosted-route"
_LOCAL_ROUTE = "example-local-route"


def _model_config(model_id: str) -> ProviderModelConfig:
    return ProviderModelConfig(id=NotBlankStr(model_id))


def _caps(*, supports_reasoning: bool) -> ModelCapabilities:
    return ModelCapabilities(
        model_id=_MODEL_ID,
        provider="example-provider",
        max_context_tokens=200_000,
        max_output_tokens=8192,
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        supports_reasoning=supports_reasoning,
    )


def _kwargs(model_id: str) -> _AcompletionKwargs:
    return {
        "model": f"{_HOSTED_ROUTE}/{model_id}",
        "messages": [{"role": "user", "content": "hello"}],
        "reasoning_effort": "high",
    }


def _route_answers(
    monkeypatch: pytest.MonkeyPatch,
    answer: Sequence[str] | Exception | None,
) -> None:
    """Pin what LiteLLM's per-route parameter lookup reports.

    The lookup reads LiteLLM's static model database, a third-party data file
    that gains and loses model rows on every release. Asserting the gate
    against a row that happens to exist today would make an upstream data
    change read as a regression here, so every answer the gate distinguishes
    is stated outright.
    """

    def _lookup(*, model: str, custom_llm_provider: str) -> Sequence[str] | None:
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(litellm, "get_supported_openai_params", _lookup)


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
    def test_dropped_when_the_model_cannot_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _route_answers(monkeypatch, ["reasoning_effort"])

        result = _apply(_MODEL_ID, supports_reasoning=False, routing_key=_HOSTED_ROUTE)

        assert "reasoning_effort" not in result

    def test_dropped_when_the_route_will_not_carry_the_parameter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Our metadata claiming reasoning is not enough to send the parameter.

        An OpenAI-compatible endpoint validates request parameters against
        LiteLLM's own view of the model. A model whose published list omits
        ``reasoning_effort`` rejects it outright with a non-retryable error,
        which fails the task on turn one, so the route's answer overrides ours.
        """
        _route_answers(monkeypatch, ["temperature", "max_tokens"])

        result = _apply(_MODEL_ID, supports_reasoning=True, routing_key=_HOSTED_ROUTE)

        assert "reasoning_effort" not in result

    def test_kept_when_both_the_model_and_the_route_support_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _route_answers(monkeypatch, ["temperature", "reasoning_effort"])

        result = _apply(_MODEL_ID, supports_reasoning=True, routing_key=_HOSTED_ROUTE)

        assert result.get("reasoning_effort") == "high"

    @pytest.mark.parametrize("answer", [[], None], ids=["empty", "absent"])
    def test_kept_for_a_route_litellm_does_not_enumerate(
        self, monkeypatch: pytest.MonkeyPatch, answer: Sequence[str] | None
    ) -> None:
        """A route with no published parameter list is not evidence of refusal.

        A locally-served route answers from our own probe rather than LiteLLM's
        static database, so an empty answer there means "unknown", and
        withholding reasoning from every local model would be a regression.
        """
        _route_answers(monkeypatch, answer)

        result = _apply(_MODEL_ID, supports_reasoning=True, routing_key=_LOCAL_ROUTE)

        assert result.get("reasoning_effort") == "high"

    def test_kept_when_the_route_lookup_itself_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A lookup that cannot answer is not the route refusing.

        The gate exists to avoid a non-retryable rejection; turning a lookup
        failure into a silent capability drop would degrade every call behind
        a route LiteLLM temporarily cannot describe.
        """
        _route_answers(monkeypatch, ValueError("model database unavailable"))

        result = _apply(_MODEL_ID, supports_reasoning=True, routing_key=_HOSTED_ROUTE)

        assert result.get("reasoning_effort") == "high"
