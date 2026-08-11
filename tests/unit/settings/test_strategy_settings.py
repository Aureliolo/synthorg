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
from synthorg.settings.write_governance_policy import (
    _STRATEGY_THRESHOLD_DEFAULT,
    is_guarded,
    is_weakening,
)

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


class TestStrategySettingsAreTheSingleSource:
    """The settings are the one source: boot config carries no rival."""

    @pytest.mark.parametrize("field", ["consensus_velocity", "premortem"])
    def test_field_absent_from_the_boot_config(self, field: str) -> None:
        from synthorg.engine.strategy.models import StrategyConfig

        assert field not in StrategyConfig.model_fields

    @pytest.mark.parametrize("field", ["consensus_velocity", "premortem"])
    def test_declaring_it_in_yaml_fails_loudly(self, field: str) -> None:
        """extra="forbid" turns a stale config into an error, not a no-op."""
        from pydantic import ValidationError

        from synthorg.engine.strategy.models import StrategyConfig

        with pytest.raises(ValidationError, match=field):
            StrategyConfig.model_validate({field: {}})


class TestWeakeningWritesAreGoverned:
    """These are the org's only checks on a meeting agreeing too fast.

    Relaxing either removes independent scrutiny from every subsequent
    meeting, so it takes the same confirm-and-reason guardrail the
    completion oracle and ask policy take. Tightening stays ungoverned.
    """

    _NS = SettingNamespace.STRATEGY.value

    @pytest.mark.parametrize(
        "key",
        ["consensus_velocity_threshold", "premortem_participants"],
    )
    def test_the_weakenable_keys_are_guarded(self, key: str) -> None:
        assert is_guarded(self._NS, key) is True

    def test_the_action_key_is_not_guarded(self) -> None:
        """Its three actions have no ordering, so neither direction weakens."""
        assert is_guarded(self._NS, "consensus_velocity_action") is False

    def test_raising_the_threshold_weakens(self) -> None:
        """1.0 silences the detector without disabling anything by name."""
        assert (
            is_weakening(
                self._NS,
                "consensus_velocity_threshold",
                current="0.85",
                new="1.0",
            )
            is True
        )

    def test_lowering_the_threshold_does_not_weaken(self) -> None:
        assert (
            is_weakening(
                self._NS,
                "consensus_velocity_threshold",
                current="0.85",
                new="0.5",
            )
            is False
        )

    def test_a_first_write_is_judged_against_the_registered_default(self) -> None:
        """An unset key still has an effective value to be weakened from."""
        assert (
            is_weakening(
                self._NS,
                "consensus_velocity_threshold",
                current=None,
                new="0.99",
            )
            is True
        )

    @pytest.mark.parametrize(
        ("current", "new", "expected"),
        [
            (PremortemParticipation.ALL.value, PremortemParticipation.NONE.value, True),
            (
                PremortemParticipation.ALL.value,
                PremortemParticipation.STRATEGIC.value,
                True,
            ),
            (
                PremortemParticipation.STRATEGIC.value,
                PremortemParticipation.ALL.value,
                False,
            ),
            (PremortemParticipation.ALL.value, PremortemParticipation.ALL.value, False),
        ],
    )
    def test_premortem_scrutiny_direction(
        self, current: str, new: str, expected: bool
    ) -> None:
        assert (
            is_weakening(self._NS, "premortem_participants", current=current, new=new)
            is expected
        )

    def test_the_governance_default_matches_the_registered_one(
        self, registry: SettingsRegistry
    ) -> None:
        """A drifted default would judge the first write from the wrong base."""
        defn = registry.get(self._NS, "consensus_velocity_threshold")
        assert defn is not None
        assert float(str(defn.default)) == _STRATEGY_THRESHOLD_DEFAULT
