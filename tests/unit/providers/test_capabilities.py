"""Tests for ModelCapabilities validation."""

import pytest
from pydantic import ValidationError

from synthorg.providers.capabilities import ModelCapabilities

from .conftest import ModelCapabilitiesFactory


@pytest.mark.unit
class TestModelCapabilities:
    """Tests for ModelCapabilities validation and immutability."""

    def test_valid(self, sample_model_capabilities: ModelCapabilities) -> None:
        assert sample_model_capabilities.model_id == "test-model"
        assert sample_model_capabilities.provider == "test-provider"
        assert sample_model_capabilities.supports_tools is True

    def test_empty_model_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelCapabilities(model_id="", provider="test", max_context_tokens=1000)

    def test_empty_provider_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelCapabilities(
                model_id="test-model", provider="", max_context_tokens=1000
            )

    def test_boolean_defaults(self) -> None:
        caps = ModelCapabilities(
            model_id="basic-model", provider="test", max_context_tokens=1000
        )
        assert caps.supports_tools is False
        assert caps.supports_vision is False
        assert caps.supports_streaming is True

    @pytest.mark.parametrize("max_context_tokens", [0, -1])
    def test_non_positive_max_context_tokens_rejected(
        self, max_context_tokens: int
    ) -> None:
        with pytest.raises(ValidationError, match="max_context_tokens"):
            ModelCapabilities(
                model_id="test-model",
                provider="test",
                max_context_tokens=max_context_tokens,
            )

    def test_frozen(self, sample_model_capabilities: ModelCapabilities) -> None:
        with pytest.raises(ValidationError):
            sample_model_capabilities.provider = "other"  # type: ignore[misc]

    def test_factory(self) -> None:
        caps = ModelCapabilitiesFactory.build()
        assert isinstance(caps, ModelCapabilities)

    def test_json_roundtrip(
        self,
        sample_model_capabilities: ModelCapabilities,
    ) -> None:
        json_str = sample_model_capabilities.model_dump_json()
        restored = ModelCapabilities.model_validate_json(json_str)
        assert restored.model_id == sample_model_capabilities.model_id
        assert restored.supports_tools == sample_model_capabilities.supports_tools
