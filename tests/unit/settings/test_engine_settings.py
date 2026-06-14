"""Unit tests for engine namespace setting definitions."""

import pytest

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.registry import get_registry


@pytest.mark.unit
class TestEngineSettingDefinitions:
    """Tests for engine namespace settings registration."""

    def test_engine_namespace_exists(self) -> None:
        """ENGINE namespace is registered in the settings registry."""
        registry = get_registry()
        assert SettingNamespace.ENGINE.value in registry.namespaces()

    def test_personality_trimming_enabled_registered(self) -> None:
        """personality_trimming_enabled is a BOOLEAN setting."""
        defn = get_registry().get("engine", "personality_trimming_enabled")

        assert defn is not None
        assert defn.type == SettingType.BOOLEAN
        assert defn.default == "true"

    def test_personality_max_tokens_override_registered(self) -> None:
        """personality_max_tokens_override is an INTEGER setting."""
        defn = get_registry().get("engine", "personality_max_tokens_override")

        assert defn is not None
        assert defn.type == SettingType.INTEGER
        assert defn.default == "0"
        assert defn.min_value == 0
        assert defn.max_value == 10000

    def test_personality_trimming_notify_registered(self) -> None:
        """personality_trimming_notify is a BOOLEAN setting defaulting to true."""
        defn = get_registry().get("engine", "personality_trimming_notify")

        assert defn is not None
        assert defn.type == SettingType.BOOLEAN
        assert defn.default == "true"
        assert defn.group == "Personality Trimming"

    def test_engine_settings_contain_expected_keys(self) -> None:
        """Engine namespace registers the expected personality-trim settings.

        Uses set containment (``>=``) rather than an exact count so the test
        remains green when unrelated engine settings are added in future
        work.  The three keys below are part of the engine settings
        contract and must always be present.
        """
        registry = get_registry()
        engine_keys = {
            d.key for d in registry.list_all() if d.namespace == SettingNamespace.ENGINE
        }
        assert engine_keys >= {
            "personality_trimming_enabled",
            "personality_max_tokens_override",
            "personality_trimming_notify",
        }
