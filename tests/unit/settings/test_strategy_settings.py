"""Tests for the strategy namespace setting definitions.

The consensus-velocity and premortem policies are organisation-wide and
operator-writable, so they live in settings rather than boot config. The
registered defaults and enum members have to track the models the hooks are
built from, or an operator's write lands on a value the model rejects.
"""

import pytest

import synthorg.settings.definitions  # noqa: F401 -- trigger registration
from synthorg.engine.strategy.models import (
    ConsensusAction,
    ConsensusVelocityConfig,
    PremortemConfig,
    PremortemParticipation,
)
from synthorg.settings.enums import SettingNamespace, SettingType
from synthorg.settings.registry import SettingsRegistry, get_registry

pytestmark = pytest.mark.unit


@pytest.fixture
def registry() -> SettingsRegistry:
    return get_registry()


class TestStrategySettingsRegistered:
    """Every key the meeting protocol registry declares must exist."""

    @pytest.mark.parametrize(
        "key",
        [
            "consensus_velocity_action",
            "consensus_velocity_threshold",
            "premortem_participants",
        ],
    )
    def test_setting_exists(self, registry: SettingsRegistry, key: str) -> None:
        assert registry.get("strategy", key) is not None, f"strategy/{key} missing"

    def test_namespace_is_declared(self) -> None:
        assert SettingNamespace.STRATEGY.value == "strategy"


class TestStrategySettingsTrackTheirModels:
    """A settings value that the model refuses is a write nobody can make."""

    def test_consensus_action_enum_matches_the_model(
        self,
        registry: SettingsRegistry,
    ) -> None:
        defn = registry.get("strategy", "consensus_velocity_action")
        assert defn is not None
        assert defn.type is SettingType.ENUM
        assert set(defn.enum_values) == {member.value for member in ConsensusAction}

    def test_premortem_participation_enum_matches_the_model(
        self,
        registry: SettingsRegistry,
    ) -> None:
        defn = registry.get("strategy", "premortem_participants")
        assert defn is not None
        assert defn.type is SettingType.ENUM
        assert set(defn.enum_values) == {
            member.value for member in PremortemParticipation
        }

    def test_defaults_mirror_the_model_defaults(
        self,
        registry: SettingsRegistry,
    ) -> None:
        """A fresh install and a bare model must agree on the policy."""
        velocity = ConsensusVelocityConfig()
        premortem = PremortemConfig()
        action = registry.get("strategy", "consensus_velocity_action")
        threshold = registry.get("strategy", "consensus_velocity_threshold")
        participants = registry.get("strategy", "premortem_participants")
        assert action is not None
        assert threshold is not None
        assert participants is not None
        assert action.default == velocity.action.value
        assert float(str(threshold.default)) == velocity.threshold
        assert participants.default == premortem.participants.value

    def test_threshold_is_bounded_to_the_model_range(
        self,
        registry: SettingsRegistry,
    ) -> None:
        defn = registry.get("strategy", "consensus_velocity_threshold")
        assert defn is not None
        assert defn.type is SettingType.FLOAT
        assert defn.min_value == 0.0
        assert defn.max_value == 1.0


class TestStrategyConfigNoLongerCarriesThem:
    """One source: the settings, not a YAML field that reaches nothing."""

    @pytest.mark.parametrize("field", ["consensus_velocity", "premortem"])
    def test_field_is_gone_from_the_boot_config(self, field: str) -> None:
        from synthorg.engine.strategy.models import StrategyConfig

        assert field not in StrategyConfig.model_fields

    @pytest.mark.parametrize("field", ["consensus_velocity", "premortem"])
    def test_declaring_it_in_yaml_fails_loudly(self, field: str) -> None:
        """extra="forbid" turns a stale config into an error, not a no-op."""
        from pydantic import ValidationError

        from synthorg.engine.strategy.models import StrategyConfig

        with pytest.raises(ValidationError, match=field):
            StrategyConfig.model_validate({field: {}})
