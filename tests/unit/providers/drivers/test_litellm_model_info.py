"""Tests for the shared LiteLLM metadata extractor."""

import pytest

from synthorg.providers.drivers.litellm_model_info import extract_model_metadata
from synthorg.providers.family_parser import RegexFamilyParser

# Generic-only parser (no provider rules): vendor-free.
_PARSER = RegexFamilyParser({})


@pytest.mark.unit
class TestExtractModelMetadata:
    def test_populates_all_fields(self) -> None:
        meta = extract_model_metadata(
            {
                "supports_function_calling": True,
                "supports_vision": True,
                "supports_reasoning": True,
                "max_output_tokens": 4096,
            },
            litellm_provider="example-provider",
            model_id="examplemodel2.0",
            parser=_PARSER,
        )
        assert meta.supports_tools is True
        assert meta.supports_vision is True
        assert meta.supports_reasoning is True
        assert meta.max_output_tokens == 4096
        assert meta.family == "examplemodel"
        assert meta.generation == 2.0
        assert meta.metadata_source == "litellm"

    def test_missing_keys_yield_safe_defaults(self) -> None:
        meta = extract_model_metadata(
            {},
            litellm_provider=None,
            model_id="plainmodel",
            parser=_PARSER,
        )
        assert meta.supports_tools is False
        assert meta.supports_vision is False
        assert meta.supports_reasoning is False
        assert meta.max_output_tokens is None
        assert meta.metadata_source == "litellm"

    def test_falls_back_to_max_tokens(self) -> None:
        meta = extract_model_metadata(
            {"max_tokens": 8192},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
        )
        assert meta.max_output_tokens == 8192

    def test_non_positive_output_tokens_drop_to_none(self) -> None:
        meta = extract_model_metadata(
            {"max_output_tokens": 0},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
        )
        assert meta.max_output_tokens is None

    def test_bool_output_tokens_drop_to_none(self) -> None:
        # A boolean is not a valid token count; coercion rejects it.
        meta = extract_model_metadata(
            {"max_output_tokens": True},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
        )
        assert meta.max_output_tokens is None

    def test_source_override(self) -> None:
        meta = extract_model_metadata(
            {},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            source="preset",
        )
        assert meta.metadata_source == "preset"
