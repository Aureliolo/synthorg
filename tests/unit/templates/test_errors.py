"""Tests for template error classes."""

import pytest

from synthorg.config.errors import ConfigLocation
from synthorg.templates.errors import (
    TemplateNotFoundError,
    TemplateRenderError,
    TemplateValidationError,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestTemplateValidationErrorStr:
    """Tests for TemplateValidationError.__str__() formatting."""

    def test_no_field_errors_returns_message_only(self) -> None:
        err = TemplateValidationError("validation failed")
        assert str(err) == "validation failed"

    def test_location_with_line_and_column(self) -> None:
        loc = ConfigLocation(
            file_path="config.yaml",
            key_path="agents.ceo",
            line=10,
            column=5,
        )
        err = TemplateValidationError(
            "bad template",
            locations=(loc,),
            field_errors=(("agents.ceo", "missing name"),),
        )
        result = str(err)
        assert "bad template (1 errors):" in result
        assert "  agents.ceo: missing name" in result
        assert "    in config.yaml at line 10, column 5" in result

    def test_location_with_line_only(self) -> None:
        loc = ConfigLocation(
            file_path="config.yaml",
            key_path="budget",
            line=42,
        )
        err = TemplateValidationError(
            "invalid",
            locations=(loc,),
            field_errors=(("budget", "must be positive"),),
        )
        result = str(err)
        assert "    in config.yaml at line 42" in result
        assert "column" not in result

    def test_location_with_file_only(self) -> None:
        loc = ConfigLocation(
            file_path="config.yaml",
            key_path="name",
        )
        err = TemplateValidationError(
            "invalid",
            locations=(loc,),
            field_errors=(("name", "too short"),),
        )
        result = str(err)
        assert "    in config.yaml" in result
        assert "at line" not in result

    def test_location_does_not_match_field_error(self) -> None:
        loc = ConfigLocation(
            file_path="config.yaml",
            key_path="other_key",
            line=1,
        )
        err = TemplateValidationError(
            "mismatch",
            locations=(loc,),
            field_errors=(("unrelated_key", "bad value"),),
        )
        result = str(err)
        assert "  unrelated_key: bad value" in result
        assert "config.yaml" not in result

    def test_location_without_file_path(self) -> None:
        loc = ConfigLocation(key_path="agents.dev", line=5, column=3)
        err = TemplateValidationError(
            "no file",
            locations=(loc,),
            field_errors=(("agents.dev", "invalid role"),),
        )
        result = str(err)
        assert "  agents.dev: invalid role" in result
        assert "    in " not in result

    def test_multiple_field_errors(self) -> None:
        loc1 = ConfigLocation(file_path="a.yaml", key_path="field_a", line=1, column=2)
        loc2 = ConfigLocation(file_path="b.yaml", key_path="field_b", line=3)
        err = TemplateValidationError(
            "multi",
            locations=(loc1, loc2),
            field_errors=(
                ("field_a", "error one"),
                ("field_b", "error two"),
            ),
        )
        result = str(err)
        assert "multi (2 errors):" in result
        assert "  field_a: error one" in result
        assert "    in a.yaml at line 1, column 2" in result
        assert "  field_b: error two" in result
        assert "    in b.yaml at line 3" in result


@pytest.mark.unit
class TestTemplateErrorConstruction:
    """Basic construction tests for TemplateNotFoundError and TemplateRenderError."""

    def test_template_not_found_error(self) -> None:
        err = TemplateNotFoundError("template missing")
        assert str(err) == "template missing"
        assert err.message == "template missing"
        assert err.locations == ()

    def test_template_render_error(self) -> None:
        loc = ConfigLocation(file_path="t.yaml")
        err = TemplateRenderError("render failed", locations=(loc,))
        assert err.message == "render failed"
        assert len(err.locations) == 1
        assert err.locations[0].file_path == "t.yaml"
