"""Tests for the shared LiteLLM metadata extractor."""

from unittest.mock import patch

import pytest
import structlog.testing

from synthorg.observability.events.provider import (
    PROVIDER_MODEL_INFO_UNAVAILABLE,
    PROVIDER_MODEL_INFO_UNEXPECTED_ERROR,
)
from synthorg.providers.drivers.litellm_model_info import (
    extract_model_metadata,
    get_litellm_model_info,
)
from synthorg.providers.family_parser import RegexFamilyParser

# Generic-only parser (no provider rules): vendor-free.
_PARSER = RegexFamilyParser({})

_LOOKUP = "synthorg.providers.drivers.litellm_model_info._litellm.get_model_info"


@pytest.mark.unit
class TestUnmappedModelIsNotAFault:
    """A model absent from the pricing table is an ordinary answer.

    Reported as an unexpected error it fires on every completion for a
    self-hosted or newly-released model, burying the warnings that do mean
    something under ones that do not.
    """

    def test_an_unmapped_model_is_reported_as_a_miss(self) -> None:
        # LiteLLM raises a bare Exception for this, indistinguishable by
        # type from a real fault, so the message is the only signal.
        boom = Exception("This model isn't mapped yet. Add it here")

        with (
            patch(_LOOKUP, side_effect=boom),
            structlog.testing.capture_logs() as logs,
        ):
            assert get_litellm_model_info("test-basic-001") == {}

        events = [entry["event"] for entry in logs]
        assert PROVIDER_MODEL_INFO_UNAVAILABLE in events
        assert PROVIDER_MODEL_INFO_UNEXPECTED_ERROR not in events

    def test_a_genuine_fault_still_warns(self) -> None:
        with (
            patch(_LOOKUP, side_effect=RuntimeError("connection reset")),
            structlog.testing.capture_logs() as logs,
        ):
            assert get_litellm_model_info("test-basic-001") == {}

        events = [entry["event"] for entry in logs]
        assert PROVIDER_MODEL_INFO_UNEXPECTED_ERROR in events

    def test_a_memory_error_is_never_swallowed(self) -> None:
        with patch(_LOOKUP, side_effect=MemoryError), pytest.raises(MemoryError):
            get_litellm_model_info("test-basic-001")


@pytest.mark.unit
class TestExtractModelMetadata:
    def test_populates_all_fields(self) -> None:
        meta = extract_model_metadata(
            {
                "supports_function_calling": True,
                "supports_vision": True,
                "supports_reasoning": True,
                "supports_prompt_caching": True,
                "max_output_tokens": 4096,
            },
            litellm_provider="example-provider",
            model_id="examplemodel2.0",
            parser=_PARSER,
        )
        assert meta.supports_tools is True
        assert meta.supports_vision is True
        assert meta.supports_reasoning is True
        assert meta.supports_prompt_caching is True
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
        assert meta.supports_prompt_caching is False
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
