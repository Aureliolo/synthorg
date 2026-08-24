"""Tests for model requirements parsing and resolution."""

import pytest
from pydantic import ValidationError

from synthorg.templates.model_requirements import (
    ModelRequirement,
    parse_model_requirement,
    resolve_model_requirement,
)


@pytest.mark.unit
class TestModelRequirement:
    def test_defaults(self) -> None:
        req = ModelRequirement()
        assert req.model_id is None
        assert req.priority == "balanced"
        assert req.min_context == 0
        assert req.requires_vision is False
        assert req.requires_reasoning is False
        assert req.family is None
        assert req.model_pattern is None

    def test_rejects_tier_and_capabilities_fields(self) -> None:
        """A model is selected by capability and priority, not a tier string."""
        assert "tier" not in ModelRequirement.model_fields
        assert "capabilities" not in ModelRequirement.model_fields

    def test_tool_calling_is_not_a_requirement_axis(self) -> None:
        """Tool calling is a matcher floor, so no role can opt out of it."""
        assert "requires_tools" not in ModelRequirement.model_fields

    def test_model_id_pin(self) -> None:
        req = ModelRequirement(model_id="example-expert-001")
        assert req.model_id == "example-expert-001"

    def test_capability_and_family_fields(self) -> None:
        req = ModelRequirement(
            requires_vision=True,
            requires_reasoning=True,
            family="example-expert",
            model_pattern="example-*",
        )
        assert req.requires_vision is True
        assert req.requires_reasoning is True
        assert req.family == "example-expert"
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
        req = parse_model_requirement("example-expert-001")
        assert req.model_id == "example-expert-001"
        assert req.priority == "balanced"

    def test_string_whitespace_stripped(self) -> None:
        req = parse_model_requirement("  example-capable-001  ")
        assert req.model_id == "example-capable-001"

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
        req = parse_model_requirement({"requires_reasoning": True})
        assert req.requires_reasoning is True
        assert req.priority == "balanced"
        assert req.min_context == 0

    def test_dict_rejects_requires_tools_field(self) -> None:
        """Tool calling is a floor, so a template cannot request it as a flag."""
        with pytest.raises(ValidationError):
            parse_model_requirement({"requires_tools": True})

    def test_dict_with_capability_requirements(self) -> None:
        req = parse_model_requirement(
            {
                "requires_vision": True,
                "family": "example-expert",
                "model_pattern": "example-*",
            }
        )
        assert req.requires_vision is True
        assert req.family == "example-expert"
        assert req.model_pattern == "example-*"

    def test_dict_rejects_tier_field(self) -> None:
        with pytest.raises(ValidationError):
            parse_model_requirement({"tier": "large"})


@pytest.mark.unit
class TestResolveModelRequirement:
    def test_no_overrides(self) -> None:
        req = resolve_model_requirement()
        assert req.priority == "balanced"
        assert req.model_id is None
        assert req.requires_reasoning is False

    def test_none_overrides(self) -> None:
        req = resolve_model_requirement(None)
        assert req.priority == "balanced"

    def test_overrides_applied(self) -> None:
        req = resolve_model_requirement(
            {"priority": "quality", "min_context": 100_000, "requires_reasoning": True}
        )
        assert req.priority == "quality"
        assert req.min_context == 100_000
        assert req.requires_reasoning is True

    def test_overrides_with_model_id(self) -> None:
        req = resolve_model_requirement({"model_id": "pinned-001"})
        assert req.model_id == "pinned-001"
        # An explicit pin is clean: no capability filters are layered on
        # (the matcher selects the pinned id verbatim).
        assert req.requires_reasoning is False
        assert req.family is None
