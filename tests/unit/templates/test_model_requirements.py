"""Tests for model requirements parsing and affinity resolution."""

import pytest
from pydantic import ValidationError

from synthorg.templates.model_requirements import (
    MODEL_AFFINITY,
    ModelRequirement,
    parse_model_requirement,
    resolve_model_requirement,
)

_VISIONARY_CONTEXT_FLOOR = 100_000


@pytest.mark.unit
class TestModelRequirement:
    def test_defaults(self) -> None:
        req = ModelRequirement()
        assert req.model_id is None
        assert req.priority == "balanced"
        assert req.min_context == 0
        assert req.requires_tools is False
        assert req.requires_vision is False
        assert req.requires_reasoning is False
        assert req.family is None
        assert req.model_pattern is None

    def test_no_legacy_fields(self) -> None:
        """The removed tier-string and capabilities-tuple axes are gone."""
        assert "tier" not in ModelRequirement.model_fields
        assert "capabilities" not in ModelRequirement.model_fields

    def test_model_id_pin(self) -> None:
        req = ModelRequirement(model_id="example-large-001")
        assert req.model_id == "example-large-001"

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

    def test_blank_model_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelRequirement(model_id="   ")

    def test_frozen(self) -> None:
        req = ModelRequirement()
        with pytest.raises(ValidationError):
            req.priority = "quality"  # type: ignore[misc]

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
    def test_string_is_explicit_model_id(self) -> None:
        req = parse_model_requirement("example-large-001")
        assert req.model_id == "example-large-001"
        assert req.priority == "balanced"

    def test_string_whitespace_stripped(self) -> None:
        req = parse_model_requirement("  example-medium-001  ")
        assert req.model_id == "example-medium-001"

    def test_blank_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="non-blank"):
            parse_model_requirement("   ")

    def test_dict_full(self) -> None:
        req = parse_model_requirement(
            {
                "priority": "quality",
                "min_context": 128_000,
                "requires_reasoning": True,
            }
        )
        assert req.priority == "quality"
        assert req.min_context == 128_000
        assert req.requires_reasoning is True

    def test_dict_partial_uses_defaults(self) -> None:
        req = parse_model_requirement({"requires_tools": True})
        assert req.requires_tools is True
        assert req.priority == "balanced"
        assert req.min_context == 0

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

    def test_dict_rejects_legacy_tier(self) -> None:
        with pytest.raises(ValidationError):
            parse_model_requirement({"tier": "large"})


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
    def test_preset_affinity_priority(
        self,
        preset: str,
        expected_priority: str,
    ) -> None:
        assert MODEL_AFFINITY[preset]["priority"] == expected_priority

    def test_code_craftsman_requires_tools(self) -> None:
        assert MODEL_AFFINITY["code_craftsman"].get("requires_tools") is True

    def test_visionary_leader_profile(self) -> None:
        profile = MODEL_AFFINITY["visionary_leader"]
        assert profile["priority"] == "quality"
        assert profile["min_context"] == _VISIONARY_CONTEXT_FLOOR
        assert profile.get("requires_reasoning") is True

    def test_affinity_min_context_non_negative(self) -> None:
        for name, affinity in MODEL_AFFINITY.items():
            if "min_context" in affinity:
                min_context = affinity["min_context"]
                assert isinstance(min_context, int)
                assert min_context >= 0, f"{name} has negative min_context"


@pytest.mark.unit
class TestResolveModelRequirement:
    def test_no_preset_no_overrides(self) -> None:
        req = resolve_model_requirement()
        assert req.priority == "balanced"
        assert req.model_id is None

    def test_preset_affinity_applied(self) -> None:
        req = resolve_model_requirement("visionary_leader")
        assert req.priority == "quality"
        assert req.min_context == _VISIONARY_CONTEXT_FLOOR
        assert req.requires_reasoning is True

    def test_unknown_preset_uses_defaults(self) -> None:
        req = resolve_model_requirement("nonexistent_preset")
        assert req.priority == "balanced"

    def test_case_insensitive_preset(self) -> None:
        req = resolve_model_requirement("EAGER_LEARNER")
        assert req.priority == "speed"

    def test_none_preset(self) -> None:
        req = resolve_model_requirement(None)
        assert req.priority == "balanced"

    def test_overrides_win_over_affinity(self) -> None:
        req = resolve_model_requirement("visionary_leader", {"priority": "cost"})
        assert req.priority == "cost"
        # Non-overridden affinity defaults still apply.
        assert req.min_context == _VISIONARY_CONTEXT_FLOOR

    def test_overrides_with_model_id(self) -> None:
        req = resolve_model_requirement("code_craftsman", {"model_id": "pinned-001"})
        assert req.model_id == "pinned-001"
        # An explicit pin is clean: affinity capability flags are NOT layered
        # on (the matcher selects the pinned id verbatim, ignoring filters).
        assert req.requires_tools is False
        assert req.family is None
