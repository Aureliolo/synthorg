"""Tests that ``models_from_litellm`` enriches per-model metadata."""

from unittest.mock import patch

import pytest

from synthorg.providers.management._helpers import models_from_litellm


def _fake_model_cost() -> dict[str, object]:
    """litellm.model_cost subset carrying capability fields."""
    return {
        "test-provider/example-large-2": {
            "litellm_provider": "test-provider",
            "input_cost_per_token": 0.000015,
            "output_cost_per_token": 0.000075,
            "max_input_tokens": 200_000,
            "max_output_tokens": 8192,
            "supports_function_calling": True,
            "supports_vision": True,
        },
        "test-provider/example-small-1": {
            "litellm_provider": "test-provider",
            "input_cost_per_token": 0.000003,
            "output_cost_per_token": 0.000015,
            "max_input_tokens": 128_000,
        },
    }


@pytest.mark.unit
class TestModelsFromLitellmMetadata:
    @patch("litellm.model_cost", _fake_model_cost())
    def test_enriches_capability_metadata(self) -> None:
        result = models_from_litellm("test-provider")
        by_id = {m.id: m for m in result}

        large = by_id["example-large-2"]
        assert large.metadata.supports_tools is True
        assert large.metadata.supports_vision is True
        assert large.metadata.max_output_tokens == 8192
        assert large.metadata.generation == 2.0
        assert large.metadata.metadata_source == "litellm"

    @patch("litellm.model_cost", _fake_model_cost())
    def test_missing_capability_fields_default_safely(self) -> None:
        result = models_from_litellm("test-provider")
        by_id = {m.id: m for m in result}

        small = by_id["example-small-1"]
        assert small.metadata.supports_tools is False
        assert small.metadata.supports_vision is False
        assert small.metadata.max_output_tokens is None
        assert small.metadata.generation == 1.0
        assert small.metadata.metadata_source == "litellm"
