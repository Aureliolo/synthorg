"""Unit tests for the native web-search provider presets."""

import pytest

from synthorg.tools.web.providers.presets import (
    DEFAULT_SEARCH_PROVIDER_ID,
    SEARCH_PROVIDER_IDS,
    SEARCH_PROVIDER_PRESETS,
    get_search_preset,
)


class TestSearchPresets:
    """The declarative preset registry."""

    @pytest.mark.unit
    def test_default_is_brave_and_first(self) -> None:
        assert DEFAULT_SEARCH_PROVIDER_ID == "brave"
        assert SEARCH_PROVIDER_IDS[0] == "brave"

    @pytest.mark.unit
    def test_all_three_providers_registered(self) -> None:
        assert set(SEARCH_PROVIDER_IDS) == {"brave", "tavily", "exa"}

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
        with pytest.raises(Exception):  # noqa: B017, PT011
            preset.endpoint = "https://evil.example"  # type: ignore[misc]

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
