"""Tests for ProviderModelResponse DTO and conversion."""

import pytest
from pydantic import ValidationError

from synthorg.api.dto_providers import ProviderModelResponse, to_provider_model_response
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderModelConfig
from synthorg.providers.capabilities import ModelCapabilities


@pytest.mark.unit
class TestProviderModelResponse:
    def test_frozen(self) -> None:
        resp = ProviderModelResponse(id="test-small-001")
        with pytest.raises(ValidationError):
            resp.id = "other"  # type: ignore[misc]

    def test_defaults(self) -> None:
        resp = ProviderModelResponse(id="test-small-001")
        assert resp.alias is None
        assert resp.cost_per_1k_input == 0.0
        assert resp.cost_per_1k_output == 0.0
        assert resp.max_context == 200_000
        assert resp.estimated_latency_ms is None
        assert resp.supports_tools is False
        assert resp.supports_vision is False
        assert resp.supports_streaming is True


@pytest.mark.unit
class TestToProviderModelResponse:
    def test_with_capabilities(self) -> None:
        config = ProviderModelConfig(
            id="test-large-001",
            alias="large",
            cost_per_1k_input=0.03,
            cost_per_1k_output=0.06,
            max_context=200_000,
            estimated_latency_ms=500,
        )
        caps = ModelCapabilities(
            model_id="test-large-001",
            provider="test-provider",
            max_context_tokens=200_000,
            max_output_tokens=4096,
            supports_tools=True,
            supports_vision=True,
            supports_streaming=True,
            cost_per_1k_input=0.03,
            cost_per_1k_output=0.06,
        )
        resp = to_provider_model_response(config, caps)
        assert resp.id == "test-large-001"
        assert resp.alias == "large"
        assert resp.cost_per_1k_input == 0.03
        assert resp.cost_per_1k_output == 0.06
        assert resp.max_context == 200_000
        assert resp.estimated_latency_ms == 500
        assert resp.supports_tools is True
        assert resp.supports_vision is True
        assert resp.supports_streaming is True

    def test_without_capabilities(self) -> None:
        config = ProviderModelConfig(
            id="test-small-001",
            alias="small",
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.002,
        )
        resp = to_provider_model_response(config, None)
        assert resp.id == "test-small-001"
        assert resp.alias == "small"
        assert resp.supports_tools is False
        assert resp.supports_vision is False
        assert resp.supports_streaming is True

    def test_config_fields_preserved(self) -> None:
        config = ProviderModelConfig(
            id="test-medium-001",
            alias=None,
            cost_per_1k_input=0.01,
            cost_per_1k_output=0.02,
            max_context=128_000,
            estimated_latency_ms=250,
        )
        resp = to_provider_model_response(config)
        assert resp.alias is None
        assert resp.max_context == 128_000
        assert resp.estimated_latency_ms == 250

    def test_image_capability_and_cost_passthrough(self) -> None:
        config = ProviderModelConfig(
            id="test-image-001",
            alias="image",
            cost_per_image=0.04,
            max_context=1,
            metadata=ModelMetadata(supports_image_generation=True),
        )
        caps = ModelCapabilities(
            model_id="test-image-001",
            provider="test-provider",
            max_context_tokens=1,
            max_output_tokens=1,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
            supports_image_generation=True,
        )
        resp = to_provider_model_response(config, caps)
        assert resp.supports_image_generation is True
        assert resp.cost_per_image == 0.04

    def test_image_flag_defaults_false(self) -> None:
        resp = ProviderModelResponse(id="test-small-001")
        assert resp.supports_image_generation is False
        assert resp.cost_per_image is None
