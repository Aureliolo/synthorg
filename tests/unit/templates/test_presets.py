"""Tests for template personality presets and auto-name generation."""

import pytest

from synthorg.core.agent import PersonalityConfig
from synthorg.templates.presets import (
    _AUTO_NAMES,
    PERSONALITY_PRESETS,
    generate_auto_name,
    get_personality_preset,
)


@pytest.mark.unit
class TestGetPersonalityPreset:
    def test_valid_preset_returns_dict(self) -> None:
        result = get_personality_preset("visionary_leader")
        assert isinstance(result, dict)
        assert "traits" in result
        assert "communication_style" in result

    def test_case_insensitive(self) -> None:
        result = get_personality_preset("VISIONARY_LEADER")
        assert result == get_personality_preset("visionary_leader")

    def test_whitespace_stripped(self) -> None:
        result = get_personality_preset("  pragmatic_builder  ")
        assert result["communication_style"] == "concise"

    def test_returns_copy(self) -> None:
        a = get_personality_preset("eager_learner")
        b = get_personality_preset("eager_learner")
        assert a == b
        assert a is not b

    def test_unknown_preset_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="Unknown personality preset"):
            get_personality_preset("nonexistent")

    def test_all_presets_have_required_keys(self) -> None:
        required_keys = {"traits", "communication_style", "description"}
        for name in PERSONALITY_PRESETS:
            preset = get_personality_preset(name)
            assert required_keys.issubset(preset.keys()), f"{name} missing keys"

    def test_preset_count_at_least_20(self) -> None:
        assert len(PERSONALITY_PRESETS) >= 20

    @pytest.mark.parametrize(
        "preset_name",
        [
            "user_advocate",
            "process_optimizer",
            "growth_hacker",
            "technical_communicator",
            "systems_thinker",
        ],
    )
    def test_new_presets_produce_valid_personality_config(
        self,
        preset_name: str,
    ) -> None:
        preset = get_personality_preset(preset_name)
        config = PersonalityConfig(**preset)
        assert isinstance(config, PersonalityConfig)

    def test_all_presets_produce_valid_personality_config(self) -> None:
        for name in PERSONALITY_PRESETS:
            preset = get_personality_preset(name)
            config = PersonalityConfig(**preset)
            assert isinstance(config, PersonalityConfig), f"{name} invalid"

    def test_presets_include_big_five(self) -> None:
        big_five_keys = {
            "openness",
            "conscientiousness",
            "extraversion",
            "agreeableness",
            "stress_response",
        }
        for name in PERSONALITY_PRESETS:
            preset = get_personality_preset(name)
            assert big_five_keys.issubset(preset.keys()), (
                f"{name} missing Big Five keys"
            )


@pytest.mark.unit
class TestGenerateAutoName:
    def test_known_role_returns_from_pool(self) -> None:
        name = generate_auto_name("CEO", seed=0)
        assert isinstance(name, str)
        assert len(name) > 0

    def test_unknown_role_uses_default_pool(self) -> None:
        name = generate_auto_name("Alien Commander", seed=0)
        assert name.startswith("Agent ")

    def test_deterministic_with_seed(self) -> None:
        a = generate_auto_name("Backend Developer", seed=42)
        b = generate_auto_name("Backend Developer", seed=42)
        assert a == b

    def test_different_seeds_may_differ(self) -> None:
        names = {generate_auto_name("CEO", seed=i) for i in range(10)}
        # With 4 names in the pool, at least 2 distinct names expected.
        assert len(names) >= 2

    def test_case_insensitive_role(self) -> None:
        a = generate_auto_name("ceo", seed=0)
        b = generate_auto_name("CEO", seed=0)
        assert a == b

    def test_whitespace_stripped_from_role(self) -> None:
        a = generate_auto_name("  CEO  ", seed=0)
        b = generate_auto_name("CEO", seed=0)
        assert a == b


@pytest.mark.unit
class TestAutoNameCoverage:
    def test_auto_names_cover_all_builtin_roles(self) -> None:
        """Every role in BUILTIN_ROLES has an auto-name pool."""
        from synthorg.core.role_catalog import BUILTIN_ROLES

        pool_keys = {k for k in _AUTO_NAMES if k != "_default"}
        role_keys = {r.name.lower() for r in BUILTIN_ROLES}
        missing = role_keys - pool_keys
        assert not missing, f"Roles missing auto-name pools: {sorted(missing)}"
