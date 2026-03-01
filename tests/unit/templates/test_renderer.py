"""Tests for the two-pass template rendering pipeline."""

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from ai_company.config.schema import RootConfig
from ai_company.templates.errors import TemplateRenderError
from ai_company.templates.loader import load_template, load_template_file
from ai_company.templates.renderer import render_template

from .conftest import TEMPLATE_REQUIRED_VAR_YAML, TEMPLATE_WITH_VARIABLES_YAML

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# ── render_template basic ────────────────────────────────────────


@pytest.mark.unit
class TestRenderTemplateBasic:
    def test_render_builtin_solo_founder(self) -> None:
        loaded = load_template("solo_founder")
        config = render_template(loaded)
        assert isinstance(config, RootConfig)
        assert config.company_name == "My Company"
        assert len(config.agents) == 2

    def test_render_builtin_startup(self) -> None:
        loaded = load_template("startup")
        config = render_template(loaded)
        assert isinstance(config, RootConfig)
        assert config.company_name == "Startup Co"
        assert len(config.agents) == 5

    def test_render_all_builtins_produce_valid_root_config(self) -> None:
        from ai_company.templates.loader import BUILTIN_TEMPLATES

        for name in BUILTIN_TEMPLATES:
            loaded = load_template(name)
            config = render_template(loaded)
            assert isinstance(config, RootConfig), f"{name} failed"
            assert len(config.agents) >= 1, f"{name} has no agents"

    def test_render_returns_frozen_config(self) -> None:
        loaded = load_template("solo_founder")
        config = render_template(loaded)
        with pytest.raises(ValidationError):
            config.company_name = "Changed"  # type: ignore[misc]


# ── Variables ────────────────────────────────────────────────────


@pytest.mark.unit
class TestRenderTemplateVariables:
    def test_default_variables_applied(
        self,
        tmp_template_file: Callable[..., Path],
    ) -> None:
        path = tmp_template_file(TEMPLATE_WITH_VARIABLES_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded)
        assert config.company_name == "Default Corp"

    def test_user_variables_override_defaults(
        self,
        tmp_template_file: Callable[..., Path],
    ) -> None:
        path = tmp_template_file(TEMPLATE_WITH_VARIABLES_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded, variables={"company_name": "Acme Inc"})
        assert config.company_name == "Acme Inc"

    def test_budget_variable_applied(
        self,
        tmp_template_file: Callable[..., Path],
    ) -> None:
        path = tmp_template_file(TEMPLATE_WITH_VARIABLES_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded, variables={"budget": 100.0})
        assert config.config.budget_monthly == 100.0

    def test_required_variable_missing_raises_error(
        self,
        tmp_template_file: Callable[..., Path],
    ) -> None:
        path = tmp_template_file(TEMPLATE_REQUIRED_VAR_YAML)
        loaded = load_template_file(path)
        with pytest.raises(TemplateRenderError, match="Required template variable"):
            render_template(loaded)

    def test_required_variable_provided(
        self,
        tmp_template_file: Callable[..., Path],
    ) -> None:
        path = tmp_template_file(TEMPLATE_REQUIRED_VAR_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded, variables={"team_lead": "Alice"})
        assert isinstance(config, RootConfig)

    def test_extra_variables_passed_through(
        self,
        tmp_template_file: Callable[..., Path],
    ) -> None:
        path = tmp_template_file(TEMPLATE_WITH_VARIABLES_YAML)
        loaded = load_template_file(path)
        # Extra variables don't cause errors.
        config = render_template(
            loaded,
            variables={"company_name": "Test", "extra_key": "ignored"},
        )
        assert isinstance(config, RootConfig)


# ── Agent expansion ──────────────────────────────────────────────


@pytest.mark.unit
class TestRenderTemplateAgents:
    def test_agents_have_unique_names(self) -> None:
        loaded = load_template("startup")
        config = render_template(loaded)
        names = [a.name for a in config.agents]
        assert len(names) == len(set(names))

    def test_agent_name_from_jinja2(self) -> None:
        loaded = load_template("solo_founder")
        config = render_template(loaded, variables={"company_name": "ACME"})
        # The CEO agent's name is "{{ company_name }} CEO" → "ACME CEO".
        ceo_agents = [a for a in config.agents if a.role == "CEO"]
        assert len(ceo_agents) == 1
        assert "ACME" in ceo_agents[0].name

    def test_auto_name_for_unnamed_agents(self) -> None:
        loaded = load_template("research_lab")
        config = render_template(loaded)
        # research_lab agents don't have explicit names.
        for agent in config.agents:
            assert agent.name != ""
            assert len(agent.name) > 0


# ── Departments ──────────────────────────────────────────────────


@pytest.mark.unit
class TestRenderTemplateDepartments:
    def test_departments_included(self) -> None:
        loaded = load_template("startup")
        config = render_template(loaded)
        assert len(config.departments) >= 1

    def test_department_names(self) -> None:
        loaded = load_template("solo_founder")
        config = render_template(loaded)
        dept_names = {d.name for d in config.departments}
        assert "executive" in dept_names or "engineering" in dept_names


# ── Error cases ──────────────────────────────────────────────────


@pytest.mark.unit
class TestRenderTemplateErrors:
    def test_invalid_jinja2_raises_render_error(
        self,
        tmp_template_file: Callable[..., Path],
    ) -> None:
        bad_yaml = """\
template:
  name: "Bad Jinja"
  description: "test"
  version: "1.0.0"

  company:
    type: "custom"

  agents:
    - role: "Dev"
      name: "{{ undefined_func() | bad_filter }}"
      level: "mid"
      model: "sonnet"
      department: "engineering"
"""
        path = tmp_template_file(bad_yaml)
        loaded = load_template_file(path)
        with pytest.raises(TemplateRenderError, match="Jinja2 rendering failed"):
            render_template(loaded)
