"""Tests for provider presets."""

import pytest

from synthorg.providers.presets import (
    PROVIDER_PRESETS,
    CloudPreset,
    LocalPreset,
    candidate_urls_for,
    default_models_for,
    get_preset,
    list_featured_presets,
    list_local_presets,
    list_presets,
    list_probable_presets,
    list_soft_presets,
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
        "cerebras",
        "cohere",
        "deepseek",
        "fireworks_ai",
        "gemini",
        "groq",
        "mistral",
        "moonshot",
        "nvidia_nim",
        "ollama-cloud",
        "openai",
        "openrouter",
        "sambanova",
        "together_ai",
        "xai",
    )
    """Featured (hand-curated) cloud preset names.

    Soft presets auto-derived from ``litellm.model_cost`` are not
    listed here.  Use ``test_all_featured_presets_categorized`` for
    the categorisation invariant.
    """
    _LOCAL_PRESETS = ("lm-studio", "ollama", "vllm")

    @pytest.mark.parametrize(
        "name",
        [
            "anthropic",
            "cerebras",
            "cohere",
            "deepseek",
            "fireworks_ai",
            "gemini",
            "groq",
            "mistral",
            "moonshot",
            "nvidia_nim",
            "openai",
            "openrouter",
            "sambanova",
            "together_ai",
            "xai",
        ],
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

    def test_ollama_cloud_requires_user_supplied_base_url(self) -> None:
        """Ollama Cloud has no default base URL; the user must supply it.

        The canonical hosted endpoint is unverified -- we deliberately
        do not bake an unverified marketing URL into the form.  When a
        stable endpoint is documented we can ship a safe default and
        flip ``requires_base_url`` back to ``False``.
        """
        preset = get_preset("ollama-cloud")
        assert preset is not None
        assert isinstance(preset, CloudPreset)
        assert preset.requires_base_url is True
        assert preset.default_base_url is None

    @pytest.mark.parametrize("name", ["ollama", "lm-studio", "vllm"])
    def test_local_preset_requires_base_url(self, name: str) -> None:
        """Self-hosted local presets require a base URL."""
        preset = get_preset(name)
        assert preset is not None, f"Preset {name!r} not found"
        assert preset.requires_base_url is True

    def test_all_featured_presets_categorized(self) -> None:
        """Every featured preset must be in either the cloud or local set.

        Soft presets (auto-derived from ``litellm.model_cost``) are
        excluded from this invariant because they are dynamic; the
        hand-curated ``_CLOUD_PRESETS`` / ``_LOCAL_PRESETS`` tuples
        track the branded set only.
        """
        featured_names: set[str] = {
            str(p.name) for p in PROVIDER_PRESETS if p.is_featured
        }
        categorized: set[str] = set(self._CLOUD_PRESETS) | set(self._LOCAL_PRESETS)
        assert featured_names == categorized, (
            f"Uncategorized featured presets: {featured_names - categorized}; "
            f"phantom featured presets: {categorized - featured_names}"
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

        for name in (
            "cerebras",
            "cohere",
            "deepseek",
            "fireworks_ai",
            "gemini",
            "groq",
            "mistral",
            "moonshot",
            "nvidia_nim",
            "openai",
            "openrouter",
            "sambanova",
            "together_ai",
            "xai",
        ):
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

    def test_cloud_preset_deserialises_via_kind_discriminator(self) -> None:
        """JSON with ``kind='cloud'`` round-trips into a ``CloudPreset``.

        Pydantic discriminated unions rely on the discriminator field
        landing at deserialisation; a regression here would silently
        coerce JSON into the wrong concrete type and break consumers
        that branch on ``isinstance``.
        """
        from pydantic import TypeAdapter

        from synthorg.providers.presets import ProviderPreset

        adapter: TypeAdapter[CloudPreset | LocalPreset] = TypeAdapter(ProviderPreset)
        result = adapter.validate_python(
            {
                "kind": "cloud",
                "name": "test-cloud",
                "display_name": "Test Cloud",
                "description": "Round-trip test",
                "driver": "litellm",
                "litellm_provider": "test-cloud",
                "auth_type": "api_key",
                "supported_auth_types": ["api_key"],
            }
        )
        assert isinstance(result, CloudPreset)
        assert result.kind == "cloud"
        assert result.name == "test-cloud"

    def test_local_preset_deserialises_via_kind_discriminator(self) -> None:
        """JSON with ``kind='local'`` round-trips into a ``LocalPreset``."""
        from pydantic import TypeAdapter

        from synthorg.providers.presets import ProviderPreset

        adapter: TypeAdapter[CloudPreset | LocalPreset] = TypeAdapter(ProviderPreset)
        result = adapter.validate_python(
            {
                "kind": "local",
                "name": "test-local",
                "display_name": "Test Local",
                "description": "Round-trip test",
                "driver": "litellm",
                "litellm_provider": "openai",
                "auth_type": "none",
                "candidate_urls": ["http://localhost:9000"],
                "requires_base_url": True,
            }
        )
        assert isinstance(result, LocalPreset)
        assert result.kind == "local"
        assert result.candidate_urls == ("http://localhost:9000",)

    def test_unknown_kind_is_rejected_by_discriminator(self) -> None:
        """JSON with an unknown ``kind`` value fails fast."""
        from pydantic import TypeAdapter, ValidationError

        from synthorg.providers.presets import ProviderPreset

        adapter: TypeAdapter[CloudPreset | LocalPreset] = TypeAdapter(ProviderPreset)
        with pytest.raises(ValidationError, match=r"(discriminat|tag)"):
            adapter.validate_python(
                {
                    "kind": "satellite",
                    "name": "x",
                    "display_name": "X",
                    "description": "X",
                    "driver": "litellm",
                    "litellm_provider": "x",
                    "auth_type": "api_key",
                }
            )

    def test_list_probable_presets_invariant_all_have_candidate_urls(self) -> None:
        """Every preset in ``list_probable_presets()`` must carry URLs.

        Property-style invariant that survives future preset additions.
        Catches the regression where a new local preset is added with
        empty ``candidate_urls`` but accidentally included.
        """
        for preset in list_probable_presets():
            assert preset.candidate_urls, (
                f"Preset {preset.name!r} appears in list_probable_presets() "
                f"but has no candidate_urls"
            )

    # ── Featured / soft tier invariants ────────────────────────────

    @pytest.mark.parametrize(
        "name",
        [
            "moonshot",
            "together_ai",
            "fireworks_ai",
            "xai",
            "cohere",
            "cerebras",
            "sambanova",
            "nvidia_nim",
        ],
    )
    def test_new_branded_preset_routes_via_litellm(self, name: str) -> None:
        """Each new branded preset routes via the matching LiteLLM namespace.

        Cohere is the one curated divergence: the brand is ``cohere``
        but LiteLLM chat completions route via ``cohere_chat/`` (the
        bare ``cohere/`` namespace is the deprecated completions
        endpoint).
        """
        preset = get_preset(name)
        assert preset is not None, f"Preset {name!r} not found"
        assert isinstance(preset, CloudPreset)
        expected = "cohere_chat" if name == "cohere" else name
        assert preset.litellm_provider == expected, (
            f"Preset {name!r} should route via {expected!r}"
        )

    def test_featured_presets_are_marked_featured(self) -> None:
        """Every preset in ``list_featured_presets`` has ``is_featured=True``."""
        for preset in list_featured_presets():
            assert preset.is_featured, (
                f"Featured-list preset {preset.name!r} has is_featured=False"
            )

    def test_soft_presets_are_not_featured(self) -> None:
        """Every preset from ``list_soft_presets`` has ``is_featured=False``."""
        for preset in list_soft_presets():
            assert not preset.is_featured, (
                f"Soft preset {preset.name!r} has is_featured=True"
            )

    def test_soft_presets_are_all_cloud(self) -> None:
        """Soft presets are always ``CloudPreset``.

        Auto-derive never yields a ``LocalPreset``.
        """
        for preset in list_soft_presets():
            assert isinstance(preset, CloudPreset), (
                f"Soft preset {preset.name!r} is not a CloudPreset"
            )

    def test_soft_presets_are_api_key_only(self) -> None:
        """Auto-derived soft presets default to API-key auth."""
        from synthorg.providers.enums import AuthType

        for preset in list_soft_presets():
            assert preset.auth_type == AuthType.API_KEY
            assert preset.supported_auth_types == (AuthType.API_KEY,)

    def test_soft_presets_have_distinct_litellm_providers(self) -> None:
        """No soft preset duplicates a featured preset's litellm_provider."""
        featured_namespaces = {p.litellm_provider for p in list_featured_presets()}
        for soft in list_soft_presets():
            assert soft.litellm_provider not in featured_namespaces, (
                f"Soft preset {soft.name!r} duplicates featured "
                f"litellm_provider {soft.litellm_provider!r}"
            )

    def test_soft_presets_skip_denylist_namespaces(self) -> None:
        """Denylist namespaces (IAM-bound, OAuth-only, deprecated) are excluded."""
        soft_namespaces = {p.litellm_provider for p in list_soft_presets()}
        for denied in (
            "bedrock",
            "vertex_ai",
            "vertex_ai-anthropic_models",
            "sagemaker",
            "watsonx",
            "github_copilot",
            "ollama",
            "huggingface",
            "cohere",
            "amazon_nova",
        ):
            assert denied not in soft_namespaces, (
                f"Denied namespace {denied!r} leaked into soft presets"
            )

    def test_provider_presets_is_featured_then_soft(self) -> None:
        """``PROVIDER_PRESETS`` orders featured entries before soft entries."""
        seen_soft = False
        for preset in PROVIDER_PRESETS:
            if not preset.is_featured:
                seen_soft = True
            elif seen_soft:
                pytest.fail(
                    f"Featured preset {preset.name!r} appears after a soft preset"
                )

    def test_list_presets_returns_featured_plus_soft(self) -> None:
        """``list_presets`` is the concatenation of featured and soft tuples."""
        featured = list_featured_presets()
        soft = list_soft_presets()
        assert list_presets() == (*featured, *soft)
