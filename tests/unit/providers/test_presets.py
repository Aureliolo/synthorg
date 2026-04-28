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

    @pytest.mark.parametrize(
        "name",
        [
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
    def test_other_cloud_presets_api_key_only(self, name: str) -> None:
        """Cloud presets other than Anthropic only support API key auth."""
        from synthorg.providers.enums import AuthType

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

    def test_build_soft_presets_yields_non_excluded_namespaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Auto-derive emits one preset per non-excluded namespace.

        Synthetic catalog: a benign LiteLLM bump that drops the live
        provider count must not break this unit test.  We assert the
        builder's behaviour against a controlled input.
        """
        import litellm

        from synthorg.providers.preset_softlist import build_soft_presets
        from synthorg.providers.presets import _FEATURED_PRESETS

        fake_cost = {
            f"model-{i}": {
                "mode": "chat",
                "litellm_provider": f"synth_provider_{i}",
            }
            for i in range(10)
        }
        monkeypatch.setattr(litellm, "model_cost", fake_cost)
        softs = build_soft_presets(_FEATURED_PRESETS)
        synth_names = {p.litellm_provider for p in softs}
        for i in range(10):
            assert f"synth_provider_{i}" in synth_names

    def test_humanise_namespace_title_cases_separators(self) -> None:
        """Underscores and hyphens become spaces, then title-cased."""
        from synthorg.providers.preset_softlist import _humanise_namespace

        assert _humanise_namespace("simpleword") == "Simpleword"
        assert _humanise_namespace("multi-token-input") == "Multi Token Input"

    def test_humanise_namespace_preserves_acronyms(self) -> None:
        """Known acronyms stay fully uppercased after the title-case pass.

        Test inputs use generic placeholders that exercise the
        acronym-restoration paths (bare acronym, acronym after a
        separator) without naming a specific vendor.  Real vendor
        strings flow through this helper at runtime via the soft-list
        module; here we test the transformation rules in isolation.
        """
        from synthorg.providers.preset_softlist import _humanise_namespace

        # Bare acronym stays uppercase.
        assert _humanise_namespace("ai") == "AI"
        assert _humanise_namespace("api") == "API"
        # Acronym after an underscore separator.
        assert _humanise_namespace("test_ai") == "Test AI"
        assert _humanise_namespace("test_llm") == "Test LLM"
        # Acronym after a hyphen separator.
        assert _humanise_namespace("test-nim") == "Test NIM"

    def test_humanise_namespace_lowercase_overrides(self) -> None:
        """Tokens listed in the lowercase override map are de-titled.

        The placeholder ``"v0"`` is a generic short token; it tests
        that the override map wins over the default title-casing.
        """
        from synthorg.providers.preset_softlist import _humanise_namespace

        assert _humanise_namespace("v0") == "v0"

    def test_is_denied_namespace_exact_match(self) -> None:
        """Exact denylist entries are denied."""
        from synthorg.providers.preset_softlist import _is_denied_namespace

        assert _is_denied_namespace("bedrock")
        assert _is_denied_namespace("github_copilot")
        assert _is_denied_namespace("nlp_cloud")
        assert _is_denied_namespace("cohere")
        assert _is_denied_namespace("amazon_nova")

    def test_is_denied_namespace_prefix_match(self) -> None:
        """Sub-namespaces inheriting a deny prefix are denied."""
        from synthorg.providers.preset_softlist import _is_denied_namespace

        assert _is_denied_namespace("bedrock_mantle")
        assert _is_denied_namespace("vertex_ai-anthropic_models")
        assert _is_denied_namespace("vertex_ai-openai_models")
        assert _is_denied_namespace("sagemaker_chat")
        assert _is_denied_namespace("watsonx_text")
        assert _is_denied_namespace("text-completion-codestral")

    def test_is_denied_namespace_allowlist(self) -> None:
        """Unrelated and curated-divergent namespaces are not denied.

        The synthetic placeholders cover the negative behaviour of the
        predicate without coupling the test to specific runtime preset
        names.  The single real-name assertion (``"cohere_chat"``)
        guards a deliberate divergence: the soft-list denylist
        contains bare ``"cohere"`` (LiteLLM's deprecated completions
        endpoint) but not ``"cohere_chat"`` (our curated chat
        namespace); a regression that accidentally promotes the deny
        rule from the bare name to the chat namespace would silently
        knock the curated Cohere preset off the picker.
        """
        from synthorg.providers.preset_softlist import _is_denied_namespace

        assert not _is_denied_namespace("test-allowed-namespace")
        assert not _is_denied_namespace("synthetic-provider")
        assert not _is_denied_namespace("example_provider")
        # Regression guard for the cohere/cohere_chat divergence.
        assert not _is_denied_namespace("cohere_chat")

    def test_iter_litellm_chat_namespaces_filters_non_chat_modes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Embedding / image / audio / rerank entries are skipped."""
        import litellm

        from synthorg.providers.preset_softlist import _iter_litellm_chat_namespaces

        chat = {"mode": "chat", "litellm_provider": "alpha"}
        embed = {"mode": "embedding", "litellm_provider": "beta"}
        image = {"mode": "image_generation", "litellm_provider": "gamma"}
        audio = {"mode": "audio_transcription", "litellm_provider": "delta"}
        rerank = {"mode": "rerank", "litellm_provider": "epsilon"}
        completion = {"mode": "completion", "litellm_provider": "zeta"}
        monkeypatch.setattr(
            litellm,
            "model_cost",
            {
                "chat-model": chat,
                "embed-model": embed,
                "image-model": image,
                "audio-model": audio,
                "rerank-model": rerank,
                "completion-model": completion,
            },
        )
        result = _iter_litellm_chat_namespaces()
        assert result == ("alpha", "zeta")

    def test_iter_litellm_chat_namespaces_handles_malformed_entries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-dict, missing-field, and empty-string entries are skipped."""
        import litellm

        from synthorg.providers.preset_softlist import _iter_litellm_chat_namespaces

        monkeypatch.setattr(
            litellm,
            "model_cost",
            {
                "good": {"mode": "chat", "litellm_provider": "alpha"},
                "missing-provider": {"mode": "chat"},
                "empty-provider": {"mode": "chat", "litellm_provider": ""},
                "none-provider": {"mode": "chat", "litellm_provider": None},
                "missing-mode": {"litellm_provider": "delta"},
                "not-a-dict": "this is a string",
            },
        )
        result = _iter_litellm_chat_namespaces()
        assert result == ("alpha",)

    def test_iter_litellm_chat_namespaces_handles_none_model_cost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``litellm.model_cost`` being ``None`` returns an empty tuple."""
        import litellm

        from synthorg.providers.preset_softlist import _iter_litellm_chat_namespaces

        monkeypatch.setattr(litellm, "model_cost", None)
        assert _iter_litellm_chat_namespaces() == ()

    def test_iter_litellm_chat_namespaces_handles_non_mapping_model_cost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-Mapping ``litellm.model_cost`` returns an empty tuple.

        Guards against a future LiteLLM upgrade that replaces the
        catalog with a list / tuple / string / other shape.  Calling
        ``.values()`` on those would raise ``AttributeError`` and
        crash app startup.
        """
        import litellm

        from synthorg.providers.preset_softlist import _iter_litellm_chat_namespaces

        for non_mapping in (["a", "b"], ("a", "b"), "string", 42):
            monkeypatch.setattr(litellm, "model_cost", non_mapping)
            assert _iter_litellm_chat_namespaces() == ()

    def test_soft_preset_validator_rejects_non_api_key_auth(self) -> None:
        """Constructing a soft CloudPreset with non-API_KEY auth fails."""
        from pydantic import ValidationError

        from synthorg.providers.enums import AuthType

        with pytest.raises(ValidationError, match=r"Soft preset.*API_KEY"):
            CloudPreset(
                name="bad-soft",
                display_name="Bad Soft",
                description="Soft preset with subscription auth",
                driver="litellm",
                litellm_provider="bad-soft",
                auth_type=AuthType.SUBSCRIPTION,
                supported_auth_types=(AuthType.API_KEY, AuthType.SUBSCRIPTION),
                is_featured=False,
            )

    def test_soft_preset_validator_rejects_extended_supported_types(self) -> None:
        """Constructing a soft CloudPreset with extra supported_auth_types fails."""
        from pydantic import ValidationError

        from synthorg.providers.enums import AuthType

        with pytest.raises(ValidationError, match=r"supported_auth_types"):
            CloudPreset(
                name="bad-soft-2",
                display_name="Bad Soft 2",
                description="Soft preset with extra auth types",
                driver="litellm",
                litellm_provider="bad-soft-2",
                auth_type=AuthType.API_KEY,
                supported_auth_types=(AuthType.API_KEY, AuthType.OAUTH),
                is_featured=False,
            )

    def test_build_soft_presets_drops_denylisted_namespaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The build helper actually filters denylisted namespaces.

        Synthetic catalog with one allowed and one denied entry; the
        builder must surface only the allowed one.  This catches
        denylist regressions independent of whatever LiteLLM ships.
        """
        import litellm

        from synthorg.providers.preset_softlist import (
            _LITELLM_NAMESPACE_DENYLIST,
            build_soft_presets,
        )
        from synthorg.providers.presets import _FEATURED_PRESETS

        denied = next(iter(_LITELLM_NAMESPACE_DENYLIST))
        fake_cost = {
            "good-model": {"mode": "chat", "litellm_provider": "synth_allowed"},
            "bad-model": {"mode": "chat", "litellm_provider": denied},
        }
        monkeypatch.setattr(litellm, "model_cost", fake_cost)
        softs = build_soft_presets(_FEATURED_PRESETS)
        namespaces = {p.litellm_provider for p in softs}
        assert "synth_allowed" in namespaces
        assert denied not in namespaces

    def test_build_soft_presets_drops_deny_prefix_namespaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The build helper applies prefix-based denylist matching.

        Sub-namespaces that match a prefix in
        ``_LITELLM_NAMESPACE_DENY_PREFIXES`` (e.g. ``vertex_ai-foo``)
        are excluded even if they are not in the exact denylist set.
        """
        import litellm

        from synthorg.providers.preset_softlist import build_soft_presets
        from synthorg.providers.presets import _FEATURED_PRESETS

        fake_cost = {
            "model-a": {"mode": "chat", "litellm_provider": "vertex_ai-fake"},
            "model-b": {"mode": "chat", "litellm_provider": "bedrock_fake"},
            "model-c": {"mode": "chat", "litellm_provider": "synth_allowed"},
        }
        monkeypatch.setattr(litellm, "model_cost", fake_cost)
        softs = build_soft_presets(_FEATURED_PRESETS)
        namespaces = {p.litellm_provider for p in softs}
        assert "synth_allowed" in namespaces
        assert "vertex_ai-fake" not in namespaces
        assert "bedrock_fake" not in namespaces
