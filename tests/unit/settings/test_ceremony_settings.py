"""Tests for ceremony policy setting definitions.

Verifies that the 6 ceremony-related settings are registered in the
coordination namespace with correct types and constraints.
(``ceremony_policy_overrides`` was dropped in HYG-1: the setting had
zero production consumers and never wired.)
"""

import pytest

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.engine.workflow.ceremony_policy import CeremonyStrategyType
from synthorg.engine.workflow.velocity_types import VelocityCalcType
from synthorg.settings.enums import SettingLevel, SettingType
from synthorg.settings.registry import SettingsRegistry, get_registry


@pytest.mark.unit
class TestCeremonySettingsRegistered:
    """Verify ceremony settings exist in the coordination namespace."""

    @pytest.fixture
    def registry(self) -> SettingsRegistry:
        return get_registry()

    @pytest.mark.parametrize(
        "key",
        [
            "ceremony_strategy",
            "ceremony_strategy_config",
            "ceremony_velocity_calculator",
            "ceremony_auto_transition",
            "ceremony_transition_threshold",
            "dept_ceremony_policies",
        ],
    )
    def test_ceremony_setting_exists(
        self, registry: SettingsRegistry, key: str
    ) -> None:
        defn = registry.get("coordination", key)
        assert defn is not None, f"coordination/{key} not registered"

    def test_ceremony_strategy_is_enum(self, registry: SettingsRegistry) -> None:
        defn = registry.get("coordination", "ceremony_strategy")
        assert defn is not None
        assert defn.type == SettingType.ENUM
        assert "task_driven" in defn.enum_values
        assert "calendar" in defn.enum_values
        assert "hybrid" in defn.enum_values
        assert "event_driven" in defn.enum_values
        assert "budget_driven" in defn.enum_values
        assert "throughput_adaptive" in defn.enum_values
        assert "external_trigger" in defn.enum_values
        assert "milestone_driven" in defn.enum_values
        assert len(defn.enum_values) == 8

    def test_ceremony_velocity_calculator_is_enum(
        self, registry: SettingsRegistry
    ) -> None:
        defn = registry.get("coordination", "ceremony_velocity_calculator")
        assert defn is not None
        assert defn.type == SettingType.ENUM
        assert "task_driven" in defn.enum_values
        assert "calendar" in defn.enum_values
        assert "multi_dimensional" in defn.enum_values
        assert "budget" in defn.enum_values
        assert "points_per_sprint" in defn.enum_values
        assert len(defn.enum_values) == 5

    def test_ceremony_auto_transition_is_bool(self, registry: SettingsRegistry) -> None:
        defn = registry.get("coordination", "ceremony_auto_transition")
        assert defn is not None
        assert defn.type == SettingType.BOOLEAN
        assert defn.default == "true"

    def test_ceremony_transition_threshold_is_float(
        self, registry: SettingsRegistry
    ) -> None:
        defn = registry.get("coordination", "ceremony_transition_threshold")
        assert defn is not None
        assert defn.type == SettingType.FLOAT
        assert defn.default == "1.0"
        assert defn.min_value == 0.01
        assert defn.max_value == 1.0

    def test_ceremony_strategy_config_is_json(self, registry: SettingsRegistry) -> None:
        defn = registry.get("coordination", "ceremony_strategy_config")
        assert defn is not None
        assert defn.type == SettingType.JSON
        assert defn.default == "{}"
        assert defn.level == SettingLevel.ADVANCED

    def test_dept_ceremony_policies_is_json(self, registry: SettingsRegistry) -> None:
        defn = registry.get("coordination", "dept_ceremony_policies")
        assert defn is not None
        assert defn.type == SettingType.JSON
        assert defn.default == "{}"
        assert defn.level == SettingLevel.ADVANCED

    def test_all_ceremony_settings_in_ceremony_policy_group(
        self, registry: SettingsRegistry
    ) -> None:
        defns = registry.list_namespace("coordination")
        ceremony_defns = [d for d in defns if d.group == "Ceremony Policy"]
        # HYG-1 dropped the unused ``ceremony_policy_overrides`` setting
        # (never consumed); the remaining six cover strategy selection,
        # config JSON, velocity calculator, auto-transition toggles,
        # threshold, and per-department override map.
        assert len(ceremony_defns) == 6

    def test_ceremony_strategy_default_is_task_driven(
        self, registry: SettingsRegistry
    ) -> None:
        defn = registry.get("coordination", "ceremony_strategy")
        assert defn is not None
        assert defn.default == "task_driven"

    def test_ceremony_strategy_enum_values_match_strenum(
        self, registry: SettingsRegistry
    ) -> None:
        """Verify ceremony_strategy enum_values match CeremonyStrategyType."""
        defn = registry.get("coordination", "ceremony_strategy")
        assert defn is not None
        assert set(defn.enum_values) == {m.value for m in CeremonyStrategyType}

    def test_ceremony_velocity_calculator_enum_values_match_strenum(
        self, registry: SettingsRegistry
    ) -> None:
        """Verify velocity_calculator enum_values match VelocityCalcType."""
        defn = registry.get("coordination", "ceremony_velocity_calculator")
        assert defn is not None
        assert set(defn.enum_values) == {m.value for m in VelocityCalcType}
