"""Unit tests for the native web-search provider presets."""

import pytest
from pydantic import ValidationError

from synthorg.tools.web.providers.presets import (
    RECENCY_WINDOW_DAYS,
    SEARCH_PROVIDER_IDS,
    SEARCH_PROVIDER_PRESETS,
    get_search_preset,
)


class TestSearchPresets:
    """The declarative preset registry."""

    @pytest.mark.unit
    def test_no_provider_is_privileged_as_a_default(self) -> None:
        """The setting ships blank, so no preset may claim to be the default.

        A shipped default here is a vendor the operator never chose being
        billed the moment web search is switched on.
        """
        import synthorg.settings.definitions  # noqa: F401
        from synthorg.settings.registry import get_registry

        definition = get_registry().get("tools", "web_search_provider")
        assert definition is not None
        assert definition.default == ""

    @pytest.mark.unit
    def test_all_providers_registered(self) -> None:
        assert set(SEARCH_PROVIDER_IDS) == {"brave", "tavily", "exa", "ollama"}

    @pytest.mark.unit
    def test_get_known_preset(self) -> None:
        preset = get_search_preset("tavily")
        assert preset is not None
        assert preset.id == "tavily"
        assert preset.method == "POST"

    @pytest.mark.unit
    def test_get_unknown_preset_returns_none(self) -> None:
        assert get_search_preset("does-not-exist") is None

    @pytest.mark.unit
    def test_preset_id_matches_registry_key(self) -> None:
        for key, preset in SEARCH_PROVIDER_PRESETS.items():
            assert key == preset.id

    @pytest.mark.unit
    def test_preset_is_frozen(self) -> None:
        preset = get_search_preset("brave")
        assert preset is not None
        with pytest.raises(ValidationError):
            preset.endpoint = "https://evil.example"  # type: ignore[misc]

    @pytest.mark.unit
    def test_preset_extra_mutation_does_not_leak(self) -> None:
        """A caller mutating ``extra`` must not corrupt the shared singleton.

        ``frozen=True`` blocks field reassignment but not in-place mutation of
        the ``extra`` dict, so ``get_search_preset`` returns an isolated copy.
        """
        preset = get_search_preset("exa")
        assert preset is not None
        preset.extra["injected"] = "y"
        fresh = get_search_preset("exa")
        assert fresh is not None
        assert "injected" not in fresh.extra

    @pytest.mark.unit
    def test_ollama_cap_is_lower_than_the_others(self) -> None:
        """The cap is load-bearing: this endpoint rejects a larger count."""
        ollama = get_search_preset("ollama")
        assert ollama is not None
        assert ollama.max_results_cap == 10

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("provider_id", "recency", "domains"),
        [
            ("brave", True, False),
            ("tavily", True, True),
            ("exa", True, True),
            ("ollama", False, False),
        ],
    )
    def test_declared_filter_support(
        self,
        provider_id: str,
        recency: bool,
        domains: bool,
    ) -> None:
        """Each preset declares exactly the filters its vendor implements."""
        preset = get_search_preset(provider_id)
        assert preset is not None
        assert preset.supports_recency is recency
        assert preset.supports_domain_filter is domains

    @pytest.mark.unit
    def test_keyword_freshness_presets_cover_every_window(self) -> None:
        """A keyword provider missing a window would drop that filter silently."""
        for preset in SEARCH_PROVIDER_PRESETS.values():
            if not preset.supports_recency or preset.freshness_style != "keyword":
                continue
            assert set(preset.freshness_values) == set(RECENCY_WINDOW_DAYS)

    @pytest.mark.unit
    def test_provider_setting_enum_matches_presets(self) -> None:
        """The tools.web_search_provider enum must not drift from the registry.

        The setting hardcodes its enum_values to avoid importing the preset
        registry into the settings bootstrap; this guards that copy.
        """
        import synthorg.settings.definitions  # noqa: F401
        from synthorg.settings.registry import get_registry

        definition = get_registry().get("tools", "web_search_provider")
        assert definition is not None
        assert set(definition.enum_values) == set(SEARCH_PROVIDER_IDS)
