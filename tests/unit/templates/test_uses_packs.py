# mypy: disable-error-code="explicit-any"
"""Tests for uses_packs template composition."""

from collections.abc import Callable
from typing import Any

import pytest

from synthorg.templates.loader import LoadedTemplate, _parse_template_yaml
from synthorg.templates.renderer import render_template
from synthorg.templates.schema import CompanyTemplate


@pytest.mark.unit
class TestUsesPacksField:
    """Tests for the uses_packs field on CompanyTemplate."""

    def test_defaults_to_empty(
        self, make_template_dict: Callable[..., dict[str, Any]]
    ) -> None:
        data = make_template_dict()
        tmpl = CompanyTemplate(**data)
        assert tmpl.uses_packs == ()

    def test_accepts_pack_names(
        self, make_template_dict: Callable[..., dict[str, Any]]
    ) -> None:
        data = make_template_dict(
            uses_packs=("security-team", "data-team"),
        )
        tmpl = CompanyTemplate(**data)
        assert tmpl.uses_packs == ("security-team", "data-team")

    def test_skips_agent_count_validation_with_packs(
        self, make_template_dict: Callable[..., dict[str, Any]]
    ) -> None:
        """Templates with uses_packs can have zero agents."""
        data = make_template_dict(
            agents=(),
            uses_packs=("security-team",),
        )
        tmpl = CompanyTemplate(**data)
        assert len(tmpl.agents) == 0

    def test_rejects_duplicate_pack_names(
        self, make_template_dict: Callable[..., dict[str, Any]]
    ) -> None:
        """Duplicate pack names in uses_packs are rejected."""
        data = make_template_dict(
            uses_packs=("security-team", "security-team"),
        )
        with pytest.raises(ValueError, match="Duplicate pack names"):
            CompanyTemplate(**data)


def _make_loaded(yaml_text: str) -> LoadedTemplate:
    """Parse YAML and wrap in a LoadedTemplate."""
    template = _parse_template_yaml(yaml_text, source_name="<test>")
    return LoadedTemplate(
        template=template,
        raw_yaml=yaml_text,
        source_name="<test>",
    )


@pytest.mark.unit
class TestUsesPacksRendering:
    """Tests for uses_packs resolution in the renderer."""

    def test_pack_agents_merged(self) -> None:
        """A template using a pack gets the pack's agents."""
        yaml_text = """\
template:
  name: "With Security Pack"
  description: "Uses security-team pack"
  version: "1.0.0"
  min_agents: 1
  max_agents: 20
  uses_packs:
    - "security-team"

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      name: "Dev One"
      level: "mid"
      model: "medium"
      department: "engineering"

  departments:
    - name: "engineering"
      budget_percent: 80
      head_role: "Backend Developer"
"""
        config = render_template(_make_loaded(yaml_text))
        roles = {a.role for a in config.agents}
        assert "Backend Developer" in roles
        assert "Security Engineer" in roles
        assert "Security Operations" in roles

    def test_pack_departments_merged(self) -> None:
        """A template using a pack gets the pack's departments."""
        yaml_text = """\
template:
  name: "With Data Pack"
  description: "Uses data-team pack"
  version: "1.0.0"
  min_agents: 1
  max_agents: 20
  uses_packs:
    - "data-team"

  company:
    type: "custom"

  agents:
    - role: "CEO"
      name: "Boss"
      level: "c_suite"
      model: "large"
      department: "executive"

  departments:
    - name: "executive"
      budget_percent: 20
      head_role: "CEO"
"""
        config = render_template(_make_loaded(yaml_text))
        dept_names = {d.name for d in config.departments}
        assert "executive" in dept_names
        assert "data_analytics" in dept_names

    def test_child_wins_over_pack(self) -> None:
        """Child's own fields override pack fields."""
        yaml_text = """\
template:
  name: "Override Pack"
  description: "Overrides security dept budget"
  version: "1.0.0"
  min_agents: 1
  max_agents: 20
  uses_packs:
    - "security-team"

  company:
    type: "custom"

  departments:
    - name: "security"
      budget_percent: 25
      head_role: "Security Engineer"

  agents:
    - role: "Backend Developer"
      name: "Dev"
      level: "mid"
      model: "medium"
      department: "engineering"
"""
        config = render_template(_make_loaded(yaml_text))
        sec_dept = next(d for d in config.departments if d.name == "security")
        assert sec_dept.budget_percent == 25.0

    def test_backward_compatible(self) -> None:
        """Templates without uses_packs render identically."""
        from synthorg.templates import load_template

        loaded = load_template("startup")
        config = render_template(loaded)
        assert len(config.agents) >= 1
        assert len(config.departments) >= 1

    def test_multi_pack_ordering(self) -> None:
        """Multiple packs are merged in declaration order."""
        yaml_text = """\
template:
  name: "Multi Pack"
  description: "Uses two packs"
  version: "1.0.0"
  min_agents: 1
  max_agents: 30
  uses_packs:
    - "security-team"
    - "data-team"

  company:
    type: "custom"

  agents:
    - role: "CEO"
      name: "Boss"
      level: "c_suite"
      model: "large"
      department: "executive"

  departments:
    - name: "executive"
      budget_percent: 20
      head_role: "CEO"
"""
        config = render_template(_make_loaded(yaml_text))
        roles = {a.role for a in config.agents}
        # Both packs' agents should be present.
        assert "Security Engineer" in roles
        assert "Data Analyst" in roles
        dept_names = {d.name for d in config.departments}
        assert "security" in dept_names
        assert "data_analytics" in dept_names

    def test_extends_plus_uses_packs(self) -> None:
        """Template with both extends and uses_packs."""
        yaml_text = """\
template:
  name: "Extended With Packs"
  description: "Extends startup and adds security"
  version: "1.0.0"
  min_agents: 1
  max_agents: 30
  extends: "startup"
  uses_packs:
    - "security-team"

  company:
    type: "custom"
"""
        config = render_template(_make_loaded(yaml_text))
        roles = {a.role for a in config.agents}
        # Should have startup agents + security pack agents.
        assert "Security Engineer" in roles
        assert len(config.agents) >= 6  # startup 5 + security 2, minus dedup
