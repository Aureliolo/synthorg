"""Unit tests for ``build_capabilities`` (LiteLLM driver capability resolution).

Focus on the ollama path, which bypasses LiteLLM's static model DB entirely
(``info = {}``) and resolves capabilities from the persisted probe metadata,
plus the three-tier embedding detection.
"""

import pytest

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import (
    ModelCapabilityOverrides,
    ProviderModelConfig,
)
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.litellm_capabilities import build_capabilities

pytestmark = pytest.mark.unit


def _config(
    model_id: str,
    *,
    tools: bool = False,
    vision: bool = False,
    reasoning: bool = False,
    prompt_caching: bool = False,
    embeddings: bool = False,
    image_generation: bool = False,
    max_context: int = 8192,
) -> ProviderModelConfig:
    return ProviderModelConfig(
        id=model_id,
        cost_per_1k_input=0.0,
        max_context=max_context,
        metadata=ModelMetadata(
            supports_tools=tools,
            supports_vision=vision,
            supports_reasoning=reasoning,
            supports_prompt_caching=prompt_caching,
            supports_embeddings=embeddings,
            supports_image_generation=image_generation,
        ),
    )


def _ollama_caps(config: ProviderModelConfig) -> ModelCapabilities:
    return build_capabilities(
        config,
        routing_key="ollama",
        provider_name="test-provider",
    )


def test_ollama_capabilities_come_from_probe_metadata() -> None:
    # ollama bypasses litellm's static DB (no entry for locally-pulled models),
    # so the probed metadata flags survive instead of all-False guesses.
    caps = _ollama_caps(_config("qwen3:8b", tools=True, vision=True, reasoning=True))
    assert caps.supports_tools is True
    assert caps.supports_vision is True
    assert caps.supports_reasoning is True
    # With no litellm streaming info, ollama models default to streaming-capable.
    assert caps.supports_streaming is True
    assert caps.provider == "test-provider"


def test_prompt_caching_flag_threaded_from_metadata() -> None:
    assert _ollama_caps(_config("m", prompt_caching=True)).supports_prompt_caching
    assert not _ollama_caps(_config("m")).supports_prompt_caching


def test_ollama_embedding_flag_from_metadata() -> None:
    assert _ollama_caps(_config("custom", embeddings=True)).supports_embeddings is True


def test_embedding_detected_by_id_substring() -> None:
    # Even with no metadata flag, an "embed" id is treated as an embedder
    # (the id-substring last resort).
    assert _ollama_caps(_config("nomic-embed-text")).supports_embeddings is True


def test_chat_model_is_not_embedding() -> None:
    assert _ollama_caps(_config("llama3:8b")).supports_embeddings is False


def test_ollama_image_generation_flag_from_metadata() -> None:
    caps = _ollama_caps(_config("local-image-001", image_generation=True))
    assert caps.supports_image_generation is True


def test_chat_model_is_not_image_generation() -> None:
    assert _ollama_caps(_config("llama3:8b")).supports_image_generation is False


def test_image_generation_detected_by_litellm_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A hosted image model is stamped ``mode="image_generation"`` in LiteLLM's
    # static DB; the flag flows through even when the persisted metadata omits it.
    def _fake_info(model: str) -> dict[str, object]:
        assert model
        return {"mode": "image_generation"}

    monkeypatch.setattr(
        "synthorg.providers.drivers.litellm_capabilities.get_litellm_model_info",
        _fake_info,
    )
    caps = build_capabilities(
        _config("hosted-image-001"),
        routing_key="example-provider",
        provider_name="example-provider",
    )
    assert caps.supports_image_generation is True


class TestCapabilityOverrides:
    """An operator override wins over the card/probe-resolved value."""

    def test_override_forces_a_capability_on(self) -> None:
        config = _config("m").model_copy(
            update={
                "capability_overrides": ModelCapabilityOverrides(
                    supports_prompt_caching=True
                )
            }
        )
        assert _ollama_caps(config).supports_prompt_caching is True

    def test_override_forces_a_capability_off(self) -> None:
        config = _config("m", tools=True).model_copy(
            update={
                "capability_overrides": ModelCapabilityOverrides(supports_tools=False)
            }
        )
        assert _ollama_caps(config).supports_tools is False

    def test_unset_override_leaves_the_resolved_value(self) -> None:
        config = _config("m", vision=True).model_copy(
            update={"capability_overrides": ModelCapabilityOverrides()}
        )
        assert _ollama_caps(config).supports_vision is True

    def test_override_wins_over_the_id_substring_embedding_detection(self) -> None:
        config = _config("nomic-embed-text").model_copy(
            update={
                "capability_overrides": ModelCapabilityOverrides(
                    supports_embeddings=False
                )
            }
        )
        assert _ollama_caps(config).supports_embeddings is False

    def test_override_wins_over_the_litellm_streaming_default(self) -> None:
        config = _config("hosted-001").model_copy(
            update={
                "capability_overrides": ModelCapabilityOverrides(
                    supports_streaming=False
                )
            }
        )
        caps = build_capabilities(
            config,
            routing_key="example-provider",
            provider_name="example-provider",
        )
        assert caps.supports_streaming is False
