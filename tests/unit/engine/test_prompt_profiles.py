"""Unit tests for prompt profile model and registry."""

from typing import get_args

import pytest
from pydantic import ValidationError

from synthorg.core.types import CapabilityLevel
from synthorg.engine.prompt_profiles import (
    PROMPT_PROFILE_REGISTRY,
    PromptProfile,
    get_prompt_profile,
)

# ── TestPromptProfile ───────────────────────────────────────────


@pytest.mark.unit
class TestPromptProfile:
    """Tests for the PromptProfile model."""

    def test_full_profile_has_expected_defaults(self) -> None:
        """Full profile enables all sections and full detail."""
        profile = get_prompt_profile("expert")

        assert profile.capability == "expert"
        assert profile.include_org_policies is True
        assert profile.simplify_acceptance_criteria is False
        assert profile.autonomy_detail_level == "full"
        assert profile.personality_mode == "full"

    def test_standard_profile_has_reduced_settings(self) -> None:
        """Standard profile condenses personality and summarizes autonomy."""
        profile = get_prompt_profile("capable")

        assert profile.capability == "capable"
        assert profile.include_org_policies is True
        assert profile.simplify_acceptance_criteria is False
        assert profile.autonomy_detail_level == "summary"
        assert profile.personality_mode == "condensed"

    def test_basic_profile_has_minimal_settings(self) -> None:
        """Basic profile strips org policies, simplifies everything."""
        profile = get_prompt_profile("basic")

        assert profile.capability == "basic"
        assert profile.include_org_policies is False
        assert profile.simplify_acceptance_criteria is True
        assert profile.autonomy_detail_level == "minimal"
        assert profile.personality_mode == "minimal"

    def test_profile_is_frozen(self) -> None:
        """PromptProfile instances are immutable."""
        profile = get_prompt_profile("expert")

        with pytest.raises(ValidationError):
            profile.capability = "basic"  # type: ignore[misc]

    def test_profile_rejects_extra_fields(self) -> None:
        """Extra fields are rejected by the model."""
        with pytest.raises(ValidationError):
            PromptProfile(
                capability="expert",
                max_personality_tokens=500,
                bogus_field="nope",  # type: ignore[call-arg]
            )

    def test_max_personality_tokens_must_be_positive(self) -> None:
        """max_personality_tokens must be > 0."""
        with pytest.raises(ValidationError):
            PromptProfile(capability="expert", max_personality_tokens=0)

    @pytest.mark.parametrize("level", ["full", "summary", "minimal"])
    def test_valid_autonomy_detail_levels(self, level: str) -> None:
        """Only full/summary/minimal are accepted."""
        profile = PromptProfile(
            capability="expert",
            max_personality_tokens=100,
            autonomy_detail_level=level,  # type: ignore[arg-type]
        )
        assert profile.autonomy_detail_level == level

    @pytest.mark.parametrize("mode", ["full", "condensed", "minimal"])
    def test_valid_personality_modes(self, mode: str) -> None:
        """Only full/condensed/minimal are accepted."""
        profile = PromptProfile(
            capability="expert",
            max_personality_tokens=100,
            personality_mode=mode,  # type: ignore[arg-type]
        )
        assert profile.personality_mode == mode


# ── TestGetPromptProfile ────────────────────────────────────────


@pytest.mark.unit
class TestGetPromptProfile:
    """Tests for the get_prompt_profile() lookup function."""

    def test_none_tier_returns_full_profile(self) -> None:
        """None tier defaults to the full (large) profile."""
        profile = get_prompt_profile(None)

        assert profile.capability == "expert"
        assert profile.personality_mode == "full"

    @pytest.mark.parametrize("tier", ["expert", "capable", "basic"])
    def test_all_tiers_return_matching_profile(self, tier: CapabilityLevel) -> None:
        """Each valid tier returns a profile with matching tier field."""
        profile = get_prompt_profile(tier)

        assert profile.capability == tier


# ── TestPromptProfileRegistry ───────────────────────────────────


@pytest.mark.unit
class TestPromptProfileRegistry:
    """Tests for the PROMPT_PROFILE_REGISTRY mapping."""

    def test_registry_covers_all_tiers(self) -> None:
        """Registry has entries for all CapabilityLevel values."""
        expected_tiers = set(get_args(CapabilityLevel))
        assert set(PROMPT_PROFILE_REGISTRY.keys()) == expected_tiers

    def test_registry_is_immutable(self) -> None:
        """Registry is a read-only mapping."""
        with pytest.raises(TypeError):
            PROMPT_PROFILE_REGISTRY["expert"] = None  # type: ignore[index]

    def test_registry_values_are_prompt_profiles(self) -> None:
        """All registry values are PromptProfile instances."""
        for profile in PROMPT_PROFILE_REGISTRY.values():
            assert isinstance(profile, PromptProfile)

    def test_profiles_have_increasing_verbosity(self) -> None:
        """Larger tiers have higher max_personality_tokens."""
        small = PROMPT_PROFILE_REGISTRY["basic"]
        medium = PROMPT_PROFILE_REGISTRY["capable"]
        large = PROMPT_PROFILE_REGISTRY["expert"]

        assert small.max_personality_tokens < medium.max_personality_tokens
        assert medium.max_personality_tokens < large.max_personality_tokens
