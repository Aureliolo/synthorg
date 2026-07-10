"""Unit tests for the LLM-assisted tier recommender."""

import json

import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.llm.metadata import ModelPinMetadata
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.providers.tier_assignment.llm_recommender import LlmTierRecommender
from tests._shared.scripted_provider import ScriptedProvider, make_text_response

pytestmark = pytest.mark.unit


def _models() -> tuple[ProviderModelConfig, ...]:
    return (
        ProviderModelConfig(
            id="tiny-7b",
            metadata=ModelMetadata(parameter_count=7_000_000_000),
        ),
        ProviderModelConfig(
            id="huge-400b",
            metadata=ModelMetadata(parameter_count=400_000_000_000),
        ),
    )


def _response(payload: dict[str, object]) -> ScriptedProvider:
    return ScriptedProvider(response=make_text_response(json.dumps(payload)))


def test_metadata_returns_pin_for_purpose() -> None:
    recommender = LlmTierRecommender(
        provider=_response({"recommendations": []}),
        model_id="classifier-model",
    )
    meta = recommender.metadata
    assert isinstance(meta, ModelPinMetadata)
    assert meta.prompt_class_id == PromptPurposeId.PROVIDERS_TIER_CLASSIFICATION


async def test_recommend_maps_response_to_recommendations() -> None:
    provider = _response(
        {
            "recommendations": [
                {
                    "model_id": "tiny-7b",
                    "tier": "small",
                    "confidence": 0.8,
                    "rationale": "small model",
                },
                {
                    "model_id": "huge-400b",
                    "tier": "large",
                    "confidence": 0.95,
                    "rationale": "frontier model",
                },
            ],
        },
    )
    recommender = LlmTierRecommender(provider=provider, model_id="classifier-model")

    result = await recommender.recommend("local-host", _models())

    by_id = {r.model_id: r for r in result}
    assert by_id["tiny-7b"].tier == "small"
    assert by_id["tiny-7b"].provider == "local-host"
    assert by_id["huge-400b"].tier == "large"


async def test_recommend_skips_models_the_model_omits() -> None:
    provider = _response(
        {
            "recommendations": [
                {
                    "model_id": "tiny-7b",
                    "tier": "small",
                    "confidence": 0.8,
                    "rationale": "small",
                },
            ],
        },
    )
    recommender = LlmTierRecommender(provider=provider, model_id="classifier-model")

    result = await recommender.recommend("local-host", _models())

    # A model the response omits is never fabricated.
    assert {r.model_id for r in result} == {"tiny-7b"}


async def test_recommend_degrades_to_empty_on_unparseable_response() -> None:
    provider = ScriptedProvider(response=make_text_response("not json at all"))
    recommender = LlmTierRecommender(provider=provider, model_id="classifier-model")

    result = await recommender.recommend("local-host", _models())

    assert result == ()


async def test_recommend_empty_models_returns_empty() -> None:
    recommender = LlmTierRecommender(
        provider=_response({"recommendations": []}),
        model_id="classifier-model",
    )
    assert await recommender.recommend("local-host", ()) == ()
