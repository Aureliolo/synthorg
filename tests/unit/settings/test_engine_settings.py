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

    @pytest.mark.parametrize(
        "key",
        ["clarification_enabled", "scoping_enabled", "ask_policy_enabled"],
    )
    def test_human_ask_toggles_default_on(self, key: str) -> None:
        """The org asks by default: all three ask toggles ship enabled.

        A run in which the system never asks is the failure this default
        exists to prevent, so the value is pinned rather than assumed.
        """
        defn = get_registry().get("engine", key)

        assert defn is not None
        assert defn.type == SettingType.BOOLEAN
        assert defn.default == "true"

    def test_ask_policy_extra_directives_registered(self) -> None:
        """ask_policy_extra_directives is a JSON setting defaulting to empty."""
        defn = get_registry().get("engine", "ask_policy_extra_directives")

        assert defn is not None
        assert defn.type == SettingType.JSON
        assert defn.default == "[]"
        assert defn.group == "Ask Policy"

    def test_engine_settings_contain_expected_keys(self) -> None:
        """Engine namespace registers the expected ask-policy settings.

        Uses set containment (``>=``) rather than an exact count so the test
        remains green when unrelated engine settings are added in future
        work.  The four keys below are part of the engine settings
        contract and must always be present.
        """
        registry = get_registry()
        engine_keys = {
            d.key for d in registry.list_all() if d.namespace == SettingNamespace.ENGINE
        }
        assert engine_keys >= {
            "clarification_enabled",
            "scoping_enabled",
            "ask_policy_enabled",
            "ask_policy_extra_directives",
        }
