"""Tests for model requirements parsing and resolution."""

import pytest
from pydantic import ValidationError

from synthorg.templates.model_requirements import (
    MODEL_AFFINITY,
    ModelRequirement,
    parse_model_requirement,
    resolve_model_requirement,
)


@pytest.mark.unit
class TestModelRequirement:
    def test_defaults(self) -> None:
        req = ModelRequirement()
        assert req.tier == "medium"
        assert req.priority == "balanced"
        assert req.min_context == 0
        assert req.capabilities == ()
        assert req.requires_tools is False
        assert req.requires_vision is False
        assert req.requires_reasoning is False
        assert req.family is None
        assert req.model_pattern is None

    def test_capability_and_family_fields(self) -> None:
        req = ModelRequirement(
            requires_tools=True,
            requires_vision=True,
            requires_reasoning=True,
            family="example-large",
            model_pattern="example-*",
        )
        assert req.requires_tools is True
        assert req.requires_vision is True
        assert req.requires_reasoning is True
        assert req.family == "example-large"
        assert req.model_pattern == "example-*"

    def test_blank_family_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequirement(family="   ")

    def test_frozen(self) -> None:
        req = ModelRequirement()
        with pytest.raises(ValidationError):
            req.tier = "large"  # type: ignore[misc]

    def test_rejects_invalid_tier(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequirement(tier="huge")  # type: ignore[arg-type]

    def test_rejects_invalid_priority(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequirement(priority="fastest")  # type: ignore[arg-type]

    def test_rejects_negative_min_context(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequirement(min_context=-1)

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequirement(unknown_field="x")  # type: ignore[call-arg]


@pytest.mark.unit
class TestParseModelRequirement:
    @pytest.mark.parametrize("tier", ["large", "medium", "small"])
    def test_string_tier(self, tier: str) -> None:
        req = parse_model_requirement(tier)
        assert req.tier == tier
        assert req.priority == "balanced"

    def test_string_case_insensitive(self) -> None:
        req = parse_model_requirement("LARGE")
        assert req.tier == "large"

    def test_string_whitespace_stripped(self) -> None:
        req = parse_model_requirement("  medium  ")
        assert req.tier == "medium"

    def test_invalid_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid model tier"):
            parse_model_requirement("huge")

    def test_dict_full(self) -> None:
        req = parse_model_requirement(
            {
                "tier": "large",
                "priority": "quality",
                "min_context": 128_000,
            }
        )
        assert req.tier == "large"
        assert req.priority == "quality"
        assert req.min_context == 128_000

    def test_dict_partial_uses_defaults(self) -> None:
        req = parse_model_requirement({"tier": "small"})
        assert req.tier == "small"
        assert req.priority == "balanced"
        assert req.min_context == 0

    def test_dict_with_capabilities(self) -> None:
        req = parse_model_requirement(
            {
                "tier": "large",
                "capabilities": ["reasoning", "tool_use"],
            }
        )
        assert req.capabilities == ("reasoning", "tool_use")

    def test_dict_with_capability_requirements(self) -> None:
        req = parse_model_requirement(
            {
                "requires_vision": True,
                "requires_tools": True,
                "family": "example-large",
                "model_pattern": "example-*",
            }
        )
        assert req.requires_vision is True
        assert req.requires_tools is True
        assert req.family == "example-large"
        assert req.model_pattern == "example-*"


@pytest.mark.unit
class TestModelAffinity:
    def test_all_presets_have_affinity(self) -> None:
        """Every personality preset should have a model affinity entry."""
        from synthorg.templates.presets import PERSONALITY_PRESETS

        missing = set(PERSONALITY_PRESETS) - set(MODEL_AFFINITY)
        assert not missing, f"Presets missing affinity: {sorted(missing)}"

    def test_affinity_values_have_valid_priority(self) -> None:
        valid = {"quality", "balanced", "speed", "cost"}
        for name, affinity in MODEL_AFFINITY.items():
            if "priority" in affinity:
                assert affinity["priority"] in valid, (
                    f"{name} has invalid priority {affinity['priority']!r}"
                )

    @pytest.mark.parametrize(
        ("preset", "expected_priority"),
        [
            ("client_advisor", "balanced"),
            ("code_craftsman", "quality"),
            ("devil_advocate", "quality"),
        ],
    )
    def test_new_preset_affinity_values(
        self,
        preset: str,
        expected_priority: str,
    ) -> None:
        assert MODEL_AFFINITY[preset]["priority"] == expected_priority

    def test_affinity_min_context_non_negative(self) -> None:
        for name, affinity in MODEL_AFFINITY.items():
            if "min_context" in affinity:
                min_context = affinity["min_context"]
                assert isinstance(min_context, int)
                assert min_context >= 0, f"{name} has negative min_context"


@pytest.mark.unit
class TestResolveModelRequirement:
    def test_bare_tier_no_preset(self) -> None:
        req = resolve_model_requirement("large")
        assert req.tier == "large"
        assert req.priority == "balanced"

    def test_tier_with_preset_affinity(self) -> None:
        req = resolve_model_requirement("medium", "visionary_leader")
        assert req.tier == "medium"
        assert req.priority == "quality"
        assert req.min_context == 100_000

    def test_unknown_preset_uses_defaults(self) -> None:
        req = resolve_model_requirement("small", "nonexistent_preset")
        assert req.tier == "small"
        assert req.priority == "balanced"

    def test_case_insensitive_preset(self) -> None:
        req = resolve_model_requirement("medium", "EAGER_LEARNER")
        assert req.priority == "speed"

    def test_none_preset(self) -> None:
        req = resolve_model_requirement("large", None)
        assert req.tier == "large"
        assert req.priority == "balanced"
