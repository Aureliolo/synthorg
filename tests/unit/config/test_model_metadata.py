"""Tests for the per-model capability metadata sub-model."""

from datetime import date

import pytest
from pydantic import ValidationError

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderModelConfig


@pytest.mark.unit
class TestModelMetadata:
    def test_defaults(self) -> None:
        meta = ModelMetadata()
        assert meta.supports_tools is False
        assert meta.supports_vision is False
        assert meta.supports_reasoning is False
        assert meta.max_output_tokens is None
        assert meta.family is None
        assert meta.generation is None
        assert meta.release_date is None
        assert meta.metadata_source == "unknown"

    def test_full_construction(self) -> None:
        meta = ModelMetadata(
            supports_tools=True,
            supports_vision=True,
            supports_reasoning=True,
            max_output_tokens=8192,
            family="claude-sonnet",
            generation=4.5,
            release_date=date(2025, 5, 14),
            metadata_source="litellm",
        )
        assert meta.supports_tools is True
        assert meta.max_output_tokens == 8192
        assert meta.family == "claude-sonnet"
        assert meta.generation == 4.5
        assert meta.release_date == date(2025, 5, 14)
        assert meta.metadata_source == "litellm"

    def test_frozen(self) -> None:
        meta = ModelMetadata()
        with pytest.raises(ValidationError):
            meta.supports_tools = True  # type: ignore[misc]

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ModelMetadata(unknown_field=True)  # type: ignore[call-arg]

    def test_generation_rejects_inf_nan(self) -> None:
        with pytest.raises(ValidationError):
            ModelMetadata(generation=float("inf"))
        with pytest.raises(ValidationError):
            ModelMetadata(generation=float("nan"))

    def test_generation_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            ModelMetadata(generation=-1.0)

    def test_max_output_tokens_rejects_zero(self) -> None:
        with pytest.raises(ValidationError):
            ModelMetadata(max_output_tokens=0)

    def test_blank_family_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelMetadata(family="   ")

    def test_invalid_source_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelMetadata(metadata_source="bogus")  # type: ignore[arg-type]


@pytest.mark.unit
class TestProviderModelConfigMetadata:
    def test_metadata_defaults_to_empty(self) -> None:
        model = ProviderModelConfig(id="example-large-001")
        assert isinstance(model.metadata, ModelMetadata)
        assert model.metadata.metadata_source == "unknown"

    def test_legacy_shape_without_metadata_validates(self) -> None:
        model = ProviderModelConfig.model_validate({"id": "example-small-001"})
        assert model.metadata == ModelMetadata()

    def test_metadata_round_trips_through_json(self) -> None:
        model = ProviderModelConfig(
            id="example-large-001",
            metadata=ModelMetadata(
                supports_vision=True,
                family="example-family",
                generation=2.0,
                metadata_source="preset",
            ),
        )
        dumped = model.model_dump(mode="json")
        restored = ProviderModelConfig.model_validate(dumped)
        assert restored.metadata.supports_vision is True
        assert restored.metadata.family == "example-family"
        assert restored.metadata.generation == 2.0
        assert restored.metadata.metadata_source == "preset"
