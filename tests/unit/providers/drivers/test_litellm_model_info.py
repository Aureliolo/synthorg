"""Tests for the shared LiteLLM metadata extractor."""

from unittest.mock import patch

import pytest
import structlog.testing

from synthorg.config.model_metadata import ModelMetadata
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

# No prior config-layer record: the base every "nothing to fall back to"
# test in this module supplies.
_NO_BASE = ModelMetadata()

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
            base=_NO_BASE,
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
            base=_NO_BASE,
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
            base=_NO_BASE,
        )
        assert meta.max_output_tokens == 8192

    def test_non_positive_output_tokens_drop_to_none(self) -> None:
        meta = extract_model_metadata(
            {"max_output_tokens": 0},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=_NO_BASE,
        )
        assert meta.max_output_tokens is None

    def test_bool_output_tokens_drop_to_none(self) -> None:
        # A boolean is not a valid token count; coercion rejects it.
        meta = extract_model_metadata(
            {"max_output_tokens": True},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=_NO_BASE,
        )
        assert meta.max_output_tokens is None

    def test_source_override(self) -> None:
        meta = extract_model_metadata(
            {},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=_NO_BASE,
            source="preset",
        )
        assert meta.metadata_source == "preset"


@pytest.mark.unit
class TestPerFieldFallback:
    """A partial card supplements the base record, never replaces it.

    Each fail-closed consumer (litellm_cache, litellm_features, llm_vision,
    the image-provider wiring) reads one of these fields directly off the
    resolved ``ModelMetadata``; losing a probe-sourced ``True`` here silently
    turns the feature off for every model litellm has ANY partial data for.
    """

    def _base(self, *, max_output_tokens: int | None = None) -> ModelMetadata:
        return ModelMetadata(
            supports_tools=True,
            supports_reasoning=True,
            supports_vision=True,
            supports_prompt_caching=True,
            supports_embeddings=True,
            supports_image_generation=True,
            tool_calls_verified=True,
            parameter_count=7_000_000_000,
            cost_tier=2,
            metadata_source="probe",
            max_output_tokens=max_output_tokens,
        )

    def test_partial_card_keeps_base_prompt_caching(self) -> None:
        meta = extract_model_metadata(
            {"supports_vision": False},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(),
        )
        assert meta.supports_prompt_caching is True

    def test_partial_card_keeps_base_reasoning(self) -> None:
        meta = extract_model_metadata(
            {"supports_vision": False},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(),
        )
        assert meta.supports_reasoning is True

    def test_partial_card_keeps_base_vision_when_silent(self) -> None:
        meta = extract_model_metadata(
            {"supports_reasoning": False},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(),
        )
        assert meta.supports_vision is True

    def test_card_asserting_vision_false_wins_over_base(self) -> None:
        # A field the card DOES speak on is not a partial gap: the fresh
        # reading is taken, never the stale base.
        meta = extract_model_metadata(
            {"supports_vision": False, "supports_reasoning": False},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(),
        )
        assert meta.supports_vision is False
        assert meta.supports_reasoning is False

    def test_partial_card_keeps_base_image_generation(self) -> None:
        meta = extract_model_metadata(
            {"supports_vision": False},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(),
        )
        assert meta.supports_image_generation is True

    def test_partial_card_keeps_base_embeddings_when_mode_absent(self) -> None:
        meta = extract_model_metadata(
            {"supports_vision": False},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(),
        )
        assert meta.supports_embeddings is True

    def test_mode_present_overrides_base_embeddings(self) -> None:
        meta = extract_model_metadata(
            {"mode": "chat"},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(),
        )
        assert meta.supports_embeddings is False

    def test_partial_card_keeps_base_max_output_tokens(self) -> None:
        meta = extract_model_metadata(
            {"supports_vision": False},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(max_output_tokens=4096),
        )
        assert meta.max_output_tokens == 4096

    def test_card_max_output_tokens_wins_over_base(self) -> None:
        meta = extract_model_metadata(
            {"max_output_tokens": 8192},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(max_output_tokens=4096),
        )
        assert meta.max_output_tokens == 8192

    def test_litellm_static_table_never_carries_runtime_fields(self) -> None:
        # tool_calls_verified / parameter_count / cost_tier have no litellm
        # equivalent, so they are unconditionally carried from base.
        meta = extract_model_metadata(
            {
                "supports_function_calling": False,
                "supports_vision": True,
                "supports_reasoning": True,
                "supports_prompt_caching": True,
            },
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(),
        )
        assert meta.tool_calls_verified is True
        assert meta.parameter_count == 7_000_000_000
        assert meta.cost_tier == 2

    def test_card_asserting_tools_false_wins_over_base(self) -> None:
        meta = extract_model_metadata(
            {"supports_function_calling": False},
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(),
        )
        assert meta.supports_tools is False

    def test_litellm_real_shape_explicit_none_falls_back_per_field(self) -> None:
        """LiteLLM always sets every key explicitly, ``None`` when unknown.

        A card silent on a capability arrives as ``{"supports_x": None,
        ...}``, never as an omitted key. A membership check on the key
        (``"x" in info``) treats an explicit ``None`` as "the card speaks"
        and collapses straight to ``False`` instead of falling back to
        base; only a ``.get(...) is not None`` check tells the two apart.
        """
        real_shape_card = {
            "supports_function_calling": None,
            "supports_vision": None,
            "supports_reasoning": None,
            "supports_prompt_caching": None,
            "mode": None,
            "max_output_tokens": None,
            "max_tokens": None,
        }
        meta = extract_model_metadata(
            real_shape_card,
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(max_output_tokens=4096),
        )
        assert meta.supports_tools is True
        assert meta.supports_vision is True
        assert meta.supports_reasoning is True
        assert meta.supports_prompt_caching is True
        assert meta.supports_embeddings is True
        assert meta.supports_image_generation is True
        assert meta.max_output_tokens == 4096

    def test_litellm_real_shape_explicit_value_wins_over_base(self) -> None:
        # A field the card genuinely speaks on, even alongside sibling
        # explicit-None fields, is not a gap: the fresh reading wins.
        real_shape_card = {
            "supports_function_calling": False,
            "supports_vision": None,
            "supports_reasoning": None,
            "supports_prompt_caching": None,
            "mode": "chat",
        }
        meta = extract_model_metadata(
            real_shape_card,
            litellm_provider=None,
            model_id="examplemodel1.0",
            parser=_PARSER,
            base=self._base(),
        )
        assert meta.supports_tools is False
        assert meta.supports_vision is True
        assert meta.supports_embeddings is False
