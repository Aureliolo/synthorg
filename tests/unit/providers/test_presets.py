"""Tests for provider presets."""

import pytest

from synthorg.providers.presets import (
    PROVIDER_PRESETS,
    CloudPreset,
    LocalPreset,
    candidate_urls_for,
    default_models_for,
    get_preset,
    list_local_presets,
    list_presets,
    list_probable_presets,
)


@pytest.mark.unit
class TestProviderPresets:
    def test_all_presets_valid_provider_configs(self) -> None:
        for preset in PROVIDER_PRESETS:
            assert preset.name
            assert preset.display_name
            assert preset.description
            assert preset.driver

    def test_preset_names_unique(self) -> None:
        names = [p.name for p in PROVIDER_PRESETS]
        assert len(names) == len(set(names))

    def test_get_preset_by_name(self) -> None:
        preset = get_preset("ollama")
        assert preset is not None
        assert preset.display_name == "Ollama"

    def test_get_preset_unknown_returns_none(self) -> None:
        assert get_preset("nonexistent") is None

    def test_list_presets_returns_all(self) -> None:
        presets = list_presets()
        assert len(presets) == len(PROVIDER_PRESETS)
        assert presets == PROVIDER_PRESETS

    def test_local_presets_have_candidate_urls(self) -> None:
        """Local presets with non-colliding ports have candidate URLs.

        vLLM is excluded: its default port (8000) is a common collision
        risk, so candidate_urls are intentionally empty.
        """
        for name in ("ollama", "lm-studio"):
            preset = get_preset(name)
            assert preset is not None, f"Preset {name!r} not found"
            assert isinstance(preset, LocalPreset)
            assert len(preset.candidate_urls) > 0, (
                f"Preset {name!r} should have candidate_urls for auto-detection"
            )
            for url in preset.candidate_urls:
                assert url.startswith(("http://", "https://")), (
                    f"candidate_url {url!r} in preset {name!r} must have http(s) scheme"
                )

    def test_vllm_preset_has_no_candidate_urls(self) -> None:
        """vLLM preset must not have candidate_urls (port 8000 collision risk)."""
        preset = get_preset("vllm")
        assert preset is not None
        assert isinstance(preset, LocalPreset)
        assert preset.candidate_urls == ()
        assert preset.default_base_url == "http://localhost:8000/v1"

    def test_cloud_presets_are_cloud_kind(self) -> None:
        """Every cloud preset must serialize with kind='cloud'."""
        for name in self._CLOUD_PRESETS:
            preset = get_preset(name)
            assert preset is not None, f"Preset {name!r} not found"
            assert isinstance(preset, CloudPreset)
            assert preset.kind == "cloud"

    def test_local_presets_are_local_kind(self) -> None:
        """Every local preset must serialize with kind='local'."""
        for name in self._LOCAL_PRESETS:
            preset = get_preset(name)
            assert preset is not None, f"Preset {name!r} not found"
            assert isinstance(preset, LocalPreset)
            assert preset.kind == "local"

    def test_candidate_urls_for_cloud_returns_empty(self) -> None:
        """The candidate_urls_for helper must return () for cloud presets."""
        for name in self._CLOUD_PRESETS:
            preset = get_preset(name)
            assert preset is not None
            assert candidate_urls_for(preset) == ()

    def test_default_models_for_local_returns_empty(self) -> None:
        """The default_models_for helper must return () for local presets."""
        for name in self._LOCAL_PRESETS:
            preset = get_preset(name)
            assert preset is not None
            assert default_models_for(preset) == ()

    def test_presets_are_frozen(self) -> None:
        from pydantic import ValidationError

        preset = get_preset("ollama")
        assert preset is not None
        with pytest.raises(ValidationError, match="frozen"):
            preset.name = "changed"  # type: ignore[misc]

    _CLOUD_PRESETS = (
        "anthropic",
        "azure",
        "deepseek",
        "gemini",
        "groq",
        "mistral",
        "ollama-cloud",
        "openai",
        "openrouter",
    )
    _LOCAL_PRESETS = ("lm-studio", "ollama", "vllm")

    @pytest.mark.parametrize(
        "name",
        ["anthropic", "deepseek", "gemini", "groq", "mistral", "openai", "openrouter"],
    )
    def test_cloud_preset_does_not_require_base_url(self, name: str) -> None:
        """Most cloud presets don't require a base URL.

        Exception: Azure (per-deployment URL) and Ollama Cloud (verifying
        the hosted base URL value) override this -- excluded from the
        parametrize set above.
        """
        preset = get_preset(name)
        assert preset is not None, f"Preset {name!r} not found"
        assert preset.requires_base_url is False

    def test_azure_requires_base_url(self) -> None:
        """Azure OpenAI requires a per-deployment base URL."""
        preset = get_preset("azure")
        assert preset is not None
        assert preset.requires_base_url is True

    def test_ollama_cloud_does_not_require_base_url(self) -> None:
        """Ollama Cloud has a default hosted base URL."""
        preset = get_preset("ollama-cloud")
        assert preset is not None
        assert isinstance(preset, CloudPreset)
        assert preset.requires_base_url is False
        assert preset.default_base_url == "https://ollama.com"

    @pytest.mark.parametrize("name", ["ollama", "lm-studio", "vllm"])
    def test_local_preset_requires_base_url(self, name: str) -> None:
        """Self-hosted local presets require a base URL."""
        preset = get_preset(name)
        assert preset is not None, f"Preset {name!r} not found"
        assert preset.requires_base_url is True

    def test_all_presets_categorized(self) -> None:
        """Every preset must be in either the cloud or local set."""
        all_names: set[str] = {str(p.name) for p in PROVIDER_PRESETS}
        categorized: set[str] = set(self._CLOUD_PRESETS) | set(self._LOCAL_PRESETS)
        assert all_names == categorized, (
            f"Uncategorized presets: {all_names - categorized}; "
            f"phantom presets: {categorized - all_names}"
        )

    def test_ollama_supports_local_model_management(self) -> None:
        """Local Ollama preset supports pull, delete, and config."""
        preset = get_preset("ollama")
        assert preset is not None
        assert isinstance(preset, LocalPreset)
        assert preset.supports_model_pull is True
        assert preset.supports_model_delete is True
        assert preset.supports_model_config is True

    def test_lm_studio_local_management_deferred(self) -> None:
        """LM Studio preset flags are False until API stabilizes."""
        preset = get_preset("lm-studio")
        assert preset is not None
        assert isinstance(preset, LocalPreset)
        assert preset.supports_model_pull is False
        assert preset.supports_model_delete is False
        assert preset.supports_model_config is False

    def test_vllm_no_local_model_management(self) -> None:
        """vLLM has no pull/delete/config API."""
        preset = get_preset("vllm")
        assert preset is not None
        assert isinstance(preset, LocalPreset)
        assert preset.supports_model_pull is False
        assert preset.supports_model_delete is False
        assert preset.supports_model_config is False

    def test_anthropic_supports_subscription_auth(self) -> None:
        """Anthropic preset supports both API key and subscription auth."""
        from synthorg.providers.enums import AuthType

        preset = get_preset("anthropic")
        assert preset is not None
        assert isinstance(preset, CloudPreset)
        assert AuthType.API_KEY in preset.supported_auth_types
        assert AuthType.SUBSCRIPTION in preset.supported_auth_types

    def test_other_cloud_presets_api_key_only(self) -> None:
        """Cloud presets other than Anthropic only support API key auth."""
        from synthorg.providers.enums import AuthType

        for name in ("openai", "gemini", "mistral", "groq", "deepseek", "openrouter"):
            preset = get_preset(name)
            assert preset is not None
            assert isinstance(preset, CloudPreset)
            assert preset.supported_auth_types == (AuthType.API_KEY,), (
                f"Preset {name!r} should be API-key only"
            )

    def test_ollama_cloud_api_key_only(self) -> None:
        """Ollama Cloud uses API key auth (no subscription flow)."""
        from synthorg.providers.enums import AuthType

        preset = get_preset("ollama-cloud")
        assert preset is not None
        assert isinstance(preset, CloudPreset)
        assert preset.supported_auth_types == (AuthType.API_KEY,)

    def test_auth_type_not_in_supported_raises(self) -> None:
        """Creating a CloudPreset with auth_type not in supported_auth_types fails."""
        from pydantic import ValidationError

        from synthorg.providers.enums import AuthType

        with pytest.raises(ValidationError, match=r"auth_type.*not in"):
            CloudPreset(
                name="test-bad-preset",
                display_name="Bad Preset",
                description="Preset with mismatched auth_type",
                driver="litellm",
                litellm_provider="test",
                auth_type=AuthType.SUBSCRIPTION,
                supported_auth_types=(AuthType.API_KEY,),
            )

    def test_list_local_presets_excludes_cloud(self) -> None:
        """list_local_presets returns only LocalPreset instances."""
        local = list_local_presets()
        assert len(local) == len(self._LOCAL_PRESETS)
        for preset in local:
            assert isinstance(preset, LocalPreset)
        assert {p.name for p in local} == set(self._LOCAL_PRESETS)

    def test_list_probable_presets_excludes_vllm(self) -> None:
        """list_probable_presets excludes vLLM (no candidate_urls).

        vLLM is intentionally manual-only because its default port (8000)
        is a common collision risk; auto-detect would be misleading.
        """
        probable = list_probable_presets()
        names = {p.name for p in probable}
        assert "vllm" not in names
        assert "ollama" in names
        assert "lm-studio" in names
        assert len(probable) == 2

    def test_ollama_cloud_routes_via_litellm_ollama(self) -> None:
        """Ollama Cloud reuses LiteLLM's ollama routing string."""
        preset = get_preset("ollama-cloud")
        assert preset is not None
        assert isinstance(preset, CloudPreset)
        assert preset.litellm_provider == "ollama"
