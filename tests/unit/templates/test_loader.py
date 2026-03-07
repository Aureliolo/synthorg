"""Tests for template loading from built-in and file-system sources."""

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ai_company.templates.errors import (
    TemplateNotFoundError,
    TemplateRenderError,
    TemplateValidationError,
)
from ai_company.templates.loader import (
    BUILTIN_TEMPLATES,
    LoadedTemplate,
    TemplateInfo,
    _to_float,
    list_builtin_templates,
    list_templates,
    load_template,
    load_template_file,
)

if TYPE_CHECKING:
    from .conftest import TemplateFileFactory

from .conftest import (
    INVALID_SYNTAX_YAML,
    MINIMAL_TEMPLATE_YAML,
    MISSING_TEMPLATE_KEY_YAML,
    TEMPLATE_WITH_VARIABLES_YAML,
)

pytestmark = pytest.mark.timeout(30)

# ── list_builtin_templates ───────────────────────────────────────


@pytest.mark.unit
class TestListBuiltinTemplates:
    def test_returns_sorted_tuple(self) -> None:
        names = list_builtin_templates()
        assert isinstance(names, tuple)
        assert names == tuple(sorted(names))

    def test_contains_all_registered(self) -> None:
        names = list_builtin_templates()
        for name in BUILTIN_TEMPLATES:
            assert name in names

    def test_count_matches_registry(self) -> None:
        assert len(list_builtin_templates()) == len(BUILTIN_TEMPLATES)


@pytest.mark.unit
class TestMinMaxPassthrough:
    def test_min_max_agents_from_yaml(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        """min_agents/max_agents pass through from YAML to TemplateMetadata."""
        yaml_with_minmax = """\
template:
  name: "MinMax Test"
  description: "test"
  version: "1.0.0"
  min_agents: 3
  max_agents: 10

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      level: "mid"
      model: "medium"
      department: "engineering"
    - role: "Frontend Developer"
      level: "mid"
      model: "medium"
      department: "engineering"
    - role: "QA Engineer"
      level: "mid"
      model: "small"
      department: "engineering"
"""
        path = tmp_template_file(yaml_with_minmax)
        loaded = load_template_file(path)
        assert loaded.template.metadata.min_agents == 3
        assert loaded.template.metadata.max_agents == 10

    def test_defaults_when_not_specified(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        """Without min/max in YAML, defaults apply (1, 100)."""
        path = tmp_template_file(MINIMAL_TEMPLATE_YAML)
        loaded = load_template_file(path)
        assert loaded.template.metadata.min_agents == 1
        assert loaded.template.metadata.max_agents == 100


# ── list_templates ───────────────────────────────────────────────


@pytest.mark.unit
class TestListTemplates:
    def test_returns_tuple_of_template_info(self) -> None:
        templates = list_templates()
        assert isinstance(templates, tuple)
        assert all(isinstance(t, TemplateInfo) for t in templates)

    def test_all_builtins_present(self) -> None:
        templates = list_templates()
        names = {t.name for t in templates}
        for builtin_name in BUILTIN_TEMPLATES:
            assert builtin_name in names

    def test_builtin_source_label(self) -> None:
        templates = list_templates()
        for t in templates:
            if t.name in BUILTIN_TEMPLATES:
                assert t.source == "builtin"

    def test_user_template_overrides_builtin(
        self,
        tmp_path: Path,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        user_dir = tmp_path / "user_templates"
        user_dir.mkdir()
        user_yaml = MINIMAL_TEMPLATE_YAML
        (user_dir / "solo_founder.yaml").write_text(user_yaml, encoding="utf-8")

        with patch(
            "ai_company.templates.loader._USER_TEMPLATES_DIR",
            user_dir,
        ):
            templates = list_templates()
            solo = next(t for t in templates if t.name == "solo_founder")
            assert solo.source == "user"


# ── load_template ────────────────────────────────────────────────


@pytest.mark.unit
class TestLoadTemplate:
    def test_load_builtin_by_name(self) -> None:
        loaded = load_template("solo_founder")
        assert isinstance(loaded, LoadedTemplate)
        assert loaded.template.metadata.name == "Solo Founder"
        assert "<builtin:" in loaded.source_name

    def test_load_builtin_case_insensitive(self) -> None:
        loaded = load_template("  Solo_Founder  ")
        assert loaded.template.metadata.name == "Solo Founder"

    def test_all_builtins_load_successfully(self) -> None:
        for name in BUILTIN_TEMPLATES:
            loaded = load_template(name)
            assert isinstance(loaded, LoadedTemplate)
            assert len(loaded.raw_yaml) > 0
            assert len(loaded.template.agents) >= 1

    def test_unknown_name_raises_not_found(self) -> None:
        with pytest.raises(TemplateNotFoundError, match="Unknown template"):
            load_template("does_not_exist")

    def test_user_template_preferred(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user_templates"
        user_dir.mkdir()
        (user_dir / "solo_founder.yaml").write_text(
            MINIMAL_TEMPLATE_YAML, encoding="utf-8"
        )

        with patch(
            "ai_company.templates.loader._USER_TEMPLATES_DIR",
            user_dir,
        ):
            loaded = load_template("solo_founder")
            # User template has "Test Template" name, not "Solo Founder".
            assert loaded.template.metadata.name == "Test Template"


# ── load_template_file ───────────────────────────────────────────


@pytest.mark.unit
class TestLoadTemplateFile:
    def test_load_from_path(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(MINIMAL_TEMPLATE_YAML)
        loaded = load_template_file(path)
        assert isinstance(loaded, LoadedTemplate)
        assert loaded.template.metadata.name == "Test Template"

    def test_load_with_variables(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(TEMPLATE_WITH_VARIABLES_YAML)
        loaded = load_template_file(path)
        assert len(loaded.template.variables) == 2
        assert loaded.template.variables[0].name == "company_name"

    def test_nonexistent_file_raises_not_found(self) -> None:
        with pytest.raises(TemplateNotFoundError, match="not found"):
            load_template_file(Path("/nonexistent/template.yaml"))

    def test_invalid_yaml_raises_render_error(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(INVALID_SYNTAX_YAML)
        with pytest.raises(TemplateRenderError, match="syntax error"):
            load_template_file(path)

    def test_missing_template_key_raises_validation_error(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(MISSING_TEMPLATE_KEY_YAML)
        with pytest.raises(TemplateValidationError, match="template"):
            load_template_file(path)

    def test_accepts_string_path(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(MINIMAL_TEMPLATE_YAML)
        loaded = load_template_file(str(path))
        assert isinstance(loaded, LoadedTemplate)


# ── LoadedTemplate dataclass ─────────────────────────────────────


@pytest.mark.unit
class TestLoadedTemplate:
    def test_frozen(self) -> None:
        loaded = load_template("solo_founder")
        with pytest.raises(AttributeError):
            loaded.source_name = "changed"  # type: ignore[misc]

    def test_raw_yaml_is_string(self) -> None:
        loaded = load_template("startup")
        assert isinstance(loaded.raw_yaml, str)
        assert "template:" in loaded.raw_yaml


# ── list_templates edge cases ────────────────────────────────────


@pytest.mark.unit
class TestListTemplatesEdgeCases:
    def test_skip_unreadable_user_template(self, tmp_path: Path) -> None:
        """User templates that raise OSError are skipped."""
        user_dir = tmp_path / "user_templates"
        user_dir.mkdir()
        tpl = user_dir / "broken.yaml"
        tpl.write_text("template:\n  name: x\n", encoding="utf-8")

        with (
            patch(
                "ai_company.templates.loader._USER_TEMPLATES_DIR",
                user_dir,
            ),
            patch(
                "ai_company.templates.loader._load_from_file",
                side_effect=OSError("disk error"),
            ),
        ):
            templates = list_templates()
            names = {t.name for t in templates}
            assert "broken" not in names

    def test_skip_invalid_user_template(self, tmp_path: Path) -> None:
        """User templates that raise TemplateRenderError are skipped."""
        user_dir = tmp_path / "user_templates"
        user_dir.mkdir()
        tpl = user_dir / "invalid.yaml"
        tpl.write_text("template:\n  name: x\n", encoding="utf-8")

        with (
            patch(
                "ai_company.templates.loader._USER_TEMPLATES_DIR",
                user_dir,
            ),
            patch(
                "ai_company.templates.loader._load_from_file",
                side_effect=TemplateRenderError("bad template"),
            ),
        ):
            templates = list_templates()
            names = {t.name for t in templates}
            assert "invalid" not in names

    def test_defective_builtin_skipped(self) -> None:
        """A defective built-in template is skipped without crashing."""
        with patch(
            "ai_company.templates.loader._load_builtin",
            side_effect=TemplateRenderError("broken builtin"),
        ):
            templates = list_templates()
            # All builtins failed, so only user templates (none) remain.
            assert templates == ()


# ── load_template path traversal ─────────────────────────────────


@pytest.mark.unit
class TestLoadTemplatePathTraversal:
    def test_posix_path_traversal_rejected(self) -> None:
        with pytest.raises(
            TemplateNotFoundError, match="must not contain path separators"
        ):
            load_template("../etc/passwd")

    def test_windows_path_traversal_rejected(self) -> None:
        with pytest.raises(
            TemplateNotFoundError, match="must not contain path separators"
        ):
            load_template("..\\etc\\passwd")


# ── _to_float ────────────────────────────────────────────────────


@pytest.mark.unit
class TestToFloat:
    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            (None, 0.0),
            ("3.14", 3.14),
            ("not-a-number", 0.0),
            ([1, 2, 3], 0.0),
        ],
        ids=["none", "valid-string", "invalid-string", "list"],
    )
    def test_to_float_coercion(self, input_val: object, expected: float) -> None:
        assert _to_float(input_val) == expected
