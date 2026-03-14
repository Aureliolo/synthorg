"""Tests for ModelCapabilities validation."""

import pytest
from pydantic import ValidationError

from synthorg.providers.capabilities import ModelCapabilities

from .conftest import ModelCapabilitiesFactory

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestModelCapabilities:
    """Tests for ModelCapabilities validation and immutability."""

    def test_valid(self, sample_model_capabilities: ModelCapabilities) -> None:
        assert sample_model_capabilities.model_id == "test-model"
        assert sample_model_capabilities.provider == "test-provider"
        assert sample_model_capabilities.max_context_tokens == 200_000
        assert sample_model_capabilities.supports_tools is True

    def test_empty_model_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelCapabilities(
                model_id="",
                provider="test",
                max_context_tokens=1000,
                max_output_tokens=500,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            )

    def test_empty_provider_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelCapabilities(
                model_id="test-model",
                provider="",
                max_context_tokens=1000,
                max_output_tokens=500,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            )

    def test_zero_context_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelCapabilities(
                model_id="test-model",
                provider="test",
                max_context_tokens=0,
                max_output_tokens=500,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            )

    def test_negative_context_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelCapabilities(
                model_id="test-model",
                provider="test",
                max_context_tokens=-1,
                max_output_tokens=500,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            )

    def test_zero_output_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelCapabilities(
                model_id="test-model",
                provider="test",
                max_context_tokens=1000,
                max_output_tokens=0,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            )

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelCapabilities(
                model_id="test-model",
                provider="test",
                max_context_tokens=1000,
                max_output_tokens=500,
                cost_per_1k_input=-0.01,
                cost_per_1k_output=0.0,
            )

    def test_boolean_defaults(self) -> None:
        caps = ModelCapabilities(
            model_id="basic-model",
            provider="test",
            max_context_tokens=4096,
            max_output_tokens=1024,
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
        )
        assert caps.supports_tools is False
        assert caps.supports_vision is False
        assert caps.supports_streaming is True
        assert caps.supports_streaming_tool_calls is False
        assert caps.supports_system_messages is True

    def test_frozen(self, sample_model_capabilities: ModelCapabilities) -> None:
        with pytest.raises(ValidationError):
            sample_model_capabilities.max_context_tokens = 999  # type: ignore[misc]

    def test_factory(self) -> None:
        caps = ModelCapabilitiesFactory.build()
        assert isinstance(caps, ModelCapabilities)

    def test_output_exceeding_context_rejected(self) -> None:
        with pytest.raises(ValidationError, match="max_output_tokens"):
            ModelCapabilities(
                model_id="test-model",
                provider="test",
                max_context_tokens=1000,
                max_output_tokens=2000,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            )

    def test_streaming_tool_calls_requires_tools(self) -> None:
        with pytest.raises(ValidationError, match="supports_tools"):
            ModelCapabilities(
                model_id="test-model",
                provider="test",
                max_context_tokens=1000,
                max_output_tokens=500,
                supports_streaming_tool_calls=True,
                supports_tools=False,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            )

    def test_streaming_tool_calls_requires_streaming(self) -> None:
        with pytest.raises(ValidationError, match="supports_streaming"):
            ModelCapabilities(
                model_id="test-model",
                provider="test",
                max_context_tokens=1000,
                max_output_tokens=500,
                supports_streaming_tool_calls=True,
                supports_tools=True,
                supports_streaming=False,
                cost_per_1k_input=0.0,
                cost_per_1k_output=0.0,
            )

    def test_json_roundtrip(
        self,
        sample_model_capabilities: ModelCapabilities,
    ) -> None:
        json_str = sample_model_capabilities.model_dump_json()
        restored = ModelCapabilities.model_validate_json(json_str)
        assert restored.model_id == sample_model_capabilities.model_id
        assert restored.cost_per_1k_input == sample_model_capabilities.cost_per_1k_input
