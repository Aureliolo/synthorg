"""Tests for template inheritance (extends) and merge logic."""

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from ai_company.config.schema import RootConfig
from ai_company.core.enums import CompanyType
from ai_company.templates.errors import TemplateInheritanceError
from ai_company.templates.loader import load_template, load_template_file
from ai_company.templates.merge import (
    _merge_agents,
    _merge_departments,
    merge_template_configs,
)
from ai_company.templates.renderer import _collect_parent_variables, render_template
from ai_company.templates.schema import (
    CompanyTemplate,
    TemplateAgentConfig,
    TemplateMetadata,
    TemplateVariable,
)

from .conftest import (
    CHILD_EXTENDS_STARTUP_YAML,
    CHILD_OVERRIDE_AGENT_YAML,
    CHILD_REMOVE_AGENT_YAML,
    CIRCULAR_SELF_YAML,
)

pytestmark = pytest.mark.timeout(30)


# ── TestMergeAgents ──────────────────────────────────────────────


@pytest.mark.unit
class TestMergeAgents:
    def test_inherit_parent_agents(self) -> None:
        """Child with no agents inherits all parent agents."""
        parent = [
            {"role": "CEO", "department": "executive"},
            {"role": "Dev", "department": "engineering"},
        ]
        result = _merge_agents(parent, [])
        assert len(result) == 2
        assert result[0]["role"] == "CEO"
        assert result[1]["role"] == "Dev"

    def test_override_by_role_dept(self) -> None:
        """Child agent replaces matching parent by (role, department)."""
        parent = [{"role": "Dev", "department": "engineering", "level": "mid"}]
        child = [{"role": "Dev", "department": "engineering", "level": "senior"}]
        result = _merge_agents(parent, child)
        assert len(result) == 1
        assert result[0]["level"] == "senior"

    def test_add_new_agent(self) -> None:
        """Unmatched child agent is appended."""
        parent = [{"role": "CEO", "department": "executive"}]
        child = [{"role": "QA Engineer", "department": "qa"}]
        result = _merge_agents(parent, child)
        assert len(result) == 2
        assert result[1]["role"] == "QA Engineer"

    def test_multiple_same_role_positional_match(self) -> None:
        """Multiple parents with same key are matched positionally."""
        parent = [
            {"role": "Dev", "department": "eng", "name": "first"},
            {"role": "Dev", "department": "eng", "name": "second"},
        ]
        child = [
            {"role": "Dev", "department": "eng", "name": "replaced-first"},
        ]
        result = _merge_agents(parent, child)
        assert len(result) == 2
        assert result[0]["name"] == "replaced-first"
        assert result[1]["name"] == "second"

    def test_remove_marker(self) -> None:
        """_remove: true removes matching parent agent."""
        parent = [
            {"role": "CEO", "department": "executive"},
            {"role": "Dev", "department": "engineering"},
        ]
        child = [{"role": "Dev", "department": "engineering", "_remove": True}]
        result = _merge_agents(parent, child)
        assert len(result) == 1
        assert result[0]["role"] == "CEO"

    def test_remove_nonexistent_raises(self) -> None:
        """_remove with no matching parent raises error."""
        parent = [{"role": "CEO", "department": "executive"}]
        child = [{"role": "QA", "department": "qa", "_remove": True}]
        with pytest.raises(TemplateInheritanceError, match="no matching parent"):
            _merge_agents(parent, child)

    def test_remove_marker_stripped_from_output(self) -> None:
        """_remove key is not in the output for non-remove agents."""
        parent = [{"role": "Dev", "department": "eng"}]
        child = [{"role": "Dev", "department": "eng", "_remove": False, "level": "sr"}]
        result = _merge_agents(parent, child)
        assert "_remove" not in result[0]


# ── TestMergeDepartments ─────────────────────────────────────────


@pytest.mark.unit
class TestMergeDepartments:
    def test_inherit_parent_departments(self) -> None:
        """Child with no departments inherits all parent depts."""
        parent = [{"name": "engineering"}, {"name": "product"}]
        result = _merge_departments(parent, [])
        assert len(result) == 2

    def test_override_by_name(self) -> None:
        """Child dept with matching name replaces parent entirely."""
        parent = [{"name": "engineering", "budget_percent": 50}]
        child = [{"name": "Engineering", "budget_percent": 80}]
        result = _merge_departments(parent, child)
        assert len(result) == 1
        assert result[0]["budget_percent"] == 80

    def test_add_new_department(self) -> None:
        """Unmatched child dept is appended."""
        parent = [{"name": "engineering"}]
        child = [{"name": "marketing"}]
        result = _merge_departments(parent, child)
        assert len(result) == 2
        assert result[1]["name"] == "marketing"


# ── TestMergeTemplateConfigs ─────────────────────────────────────


@pytest.mark.unit
class TestMergeTemplateConfigs:
    def test_scalars_child_wins(self) -> None:
        """Child scalars override parent."""
        parent: dict[str, Any] = {"company_name": "Parent Co"}
        child: dict[str, Any] = {"company_name": "Child Co"}
        result = merge_template_configs(parent, child)
        assert result["company_name"] == "Child Co"

    def test_scalars_parent_fallback(self) -> None:
        """Parent scalar used when child doesn't provide it."""
        parent: dict[str, Any] = {
            "company_name": "Parent Co",
            "company_type": "startup",
        }
        child: dict[str, Any] = {}
        result = merge_template_configs(parent, child)
        assert result["company_name"] == "Parent Co"
        assert result["company_type"] == "startup"

    def test_config_deep_merge(self) -> None:
        """Config dicts are deep-merged."""
        parent: dict[str, Any] = {
            "config": {"autonomy": 0.5, "budget_monthly": 100.0},
        }
        child: dict[str, Any] = {
            "config": {"autonomy": 0.8},
        }
        result = merge_template_configs(parent, child)
        assert result["config"]["autonomy"] == 0.8
        assert result["config"]["budget_monthly"] == 100.0

    def test_full_merge_integration(self) -> None:
        """Full merge with agents, departments, and config."""
        parent: dict[str, Any] = {
            "company_name": "Parent",
            "agents": [{"role": "CEO", "department": "exec"}],
            "departments": [{"name": "exec"}],
            "config": {"autonomy": 0.5},
        }
        child: dict[str, Any] = {
            "company_name": "Child",
            "agents": [{"role": "Dev", "department": "eng"}],
            "departments": [{"name": "eng"}],
            "config": {"budget_monthly": 200.0},
        }
        result = merge_template_configs(parent, child)
        assert result["company_name"] == "Child"
        assert len(result["agents"]) == 2
        assert len(result["departments"]) == 2
        assert result["config"]["autonomy"] == 0.5
        assert result["config"]["budget_monthly"] == 200.0

    def test_workflow_handoffs_child_replaces(self) -> None:
        """Child workflow_handoffs replace parent entirely."""
        parent: dict[str, Any] = {
            "workflow_handoffs": [{"from": "a", "to": "b"}],
        }
        child: dict[str, Any] = {
            "workflow_handoffs": [{"from": "x", "to": "y"}],
        }
        result = merge_template_configs(parent, child)
        assert len(result["workflow_handoffs"]) == 1
        assert result["workflow_handoffs"][0]["from"] == "x"

    def test_escalation_paths_parent_fallback(self) -> None:
        """Parent escalation_paths used when child doesn't provide them."""
        parent: dict[str, Any] = {
            "escalation_paths": [{"from": "eng", "to": "security"}],
        }
        child: dict[str, Any] = {}
        result = merge_template_configs(parent, child)
        assert result["escalation_paths"] == [{"from": "eng", "to": "security"}]

    def test_none_child_scalar_uses_parent(self) -> None:
        """None child scalar falls back to parent value."""
        parent: dict[str, Any] = {"company_name": "Parent Co"}
        child: dict[str, Any] = {"company_name": None}
        result = merge_template_configs(parent, child)
        assert result["company_name"] == "Parent Co"


# ── TestCollectParentVariables ────────────────────────────────────


@pytest.mark.unit
class TestCollectParentVariables:
    def test_child_vars_override_parent_defaults(self) -> None:
        """Child variables take precedence over parent defaults."""
        parent = CompanyTemplate(
            metadata=TemplateMetadata(name="P", company_type=CompanyType.CUSTOM),
            variables=(
                TemplateVariable(name="x", default="parent_x"),
                TemplateVariable(name="y", default="parent_y"),
            ),
            agents=(TemplateAgentConfig(role="Backend Developer"),),
        )
        child_vars = {"x": "child_x", "z": "child_z"}
        result = _collect_parent_variables(parent, child_vars)
        assert result["x"] == "child_x"
        assert result["y"] == "parent_y"
        assert result["z"] == "child_z"

    def test_parent_defaults_fill_gaps(self) -> None:
        """Parent defaults fill variables not in child."""
        parent = CompanyTemplate(
            metadata=TemplateMetadata(name="P", company_type=CompanyType.CUSTOM),
            variables=(TemplateVariable(name="a", default="default_a"),),
            agents=(TemplateAgentConfig(role="Backend Developer"),),
        )
        result = _collect_parent_variables(parent, {})
        assert result["a"] == "default_a"

    def test_required_parent_var_without_child_value(self) -> None:
        """Required parent var with no child value or default is omitted."""
        parent = CompanyTemplate(
            metadata=TemplateMetadata(name="P", company_type=CompanyType.CUSTOM),
            variables=(TemplateVariable(name="req", required=True),),
            agents=(TemplateAgentConfig(role="Backend Developer"),),
        )
        result = _collect_parent_variables(parent, {})
        assert "req" not in result


# ── TestResolveInheritance ───────────────────────────────────────


@pytest.mark.unit
class TestResolveInheritance:
    def test_single_level_extends(
        self,
        tmp_path: Path,
    ) -> None:
        """Child extends builtin and inherits its agents."""
        child_path = tmp_path / "child.yaml"
        child_path.write_text(CHILD_EXTENDS_STARTUP_YAML, encoding="utf-8")
        loaded = load_template_file(child_path)
        config = render_template(loaded)
        assert isinstance(config, RootConfig)
        # Should have startup's 5 agents + 1 new QA agent.
        assert len(config.agents) == 6

    def test_override_agent_via_extends(
        self,
        tmp_path: Path,
    ) -> None:
        """Child overrides a parent agent by (role, department)."""
        child_path = tmp_path / "override.yaml"
        child_path.write_text(CHILD_OVERRIDE_AGENT_YAML, encoding="utf-8")
        loaded = load_template_file(child_path)
        config = render_template(loaded)
        assert isinstance(config, RootConfig)
        # solo_founder has CEO + Full-Stack Dev. Child overrides Full-Stack Dev.
        assert len(config.agents) == 2
        fs_agents = [a for a in config.agents if a.role == "Full-Stack Developer"]
        assert len(fs_agents) == 1
        assert fs_agents[0].level.value == "lead"

    def test_remove_agent_via_extends(
        self,
        tmp_path: Path,
    ) -> None:
        """Child removes a parent agent and adds a new one."""
        child_path = tmp_path / "remove.yaml"
        child_path.write_text(CHILD_REMOVE_AGENT_YAML, encoding="utf-8")
        loaded = load_template_file(child_path)
        config = render_template(loaded)
        assert isinstance(config, RootConfig)
        # solo_founder: CEO + FS Dev. Remove FS Dev, add Backend Dev => 2 agents.
        roles = [a.role for a in config.agents]
        assert "CEO" in roles
        assert "Backend Developer" in roles
        assert "Full-Stack Developer" not in roles

    def test_variable_flow_to_parent(
        self,
        tmp_path: Path,
    ) -> None:
        """Child variables flow to parent template."""
        child_path = tmp_path / "var_child.yaml"
        child_path.write_text(CHILD_EXTENDS_STARTUP_YAML, encoding="utf-8")
        loaded = load_template_file(child_path)
        config = render_template(
            loaded,
            variables={"company_name": "Custom Name"},
        )
        # Child's company_name overrides parent's (child wins in merge).
        assert config.company_name == "Custom Name"

    def test_multi_level_extends(
        self,
        tmp_path: Path,
    ) -> None:
        """A→B→C multi-level inheritance resolves correctly."""
        # B extends startup
        child_b_yaml = CHILD_EXTENDS_STARTUP_YAML
        child_b_path = tmp_path / "child_b.yaml"
        child_b_path.write_text(child_b_yaml, encoding="utf-8")

        # A extends B (using file path won't work for name-based lookup,
        # so we use user template directory patching)
        child_a_yaml = """\
template:
  name: "Grandchild"
  description: "Two levels deep"
  version: "1.0.0"
  min_agents: 1
  max_agents: 30
  extends: "child_b"

  company:
    type: "startup"

  agents:
    - role: "Data Analyst"
      level: "mid"
      model: "medium"
      personality_preset: "data_driven_optimizer"
      department: "engineering"
"""
        child_a_path = tmp_path / "child_a.yaml"
        child_a_path.write_text(child_a_yaml, encoding="utf-8")

        # Patch user templates dir so child_b is found by name.
        with patch(
            "ai_company.templates.loader._USER_TEMPLATES_DIR",
            tmp_path,
        ):
            loaded = load_template_file(child_a_path)
            config = render_template(loaded)
            assert isinstance(config, RootConfig)
            # startup(5) + child_b adds QA(1) + child_a adds Data Analyst(1) = 7
            assert len(config.agents) == 7


# ── TestCircularDetection ────────────────────────────────────────


@pytest.mark.unit
class TestCircularDetection:
    def test_self_extends_raises(
        self,
        tmp_path: Path,
    ) -> None:
        """Template extending itself raises TemplateInheritanceError."""
        path = tmp_path / "self_loop.yaml"
        path.write_text(CIRCULAR_SELF_YAML, encoding="utf-8")

        with patch(
            "ai_company.templates.loader._USER_TEMPLATES_DIR",
            tmp_path,
        ):
            loaded = load_template_file(path)
            with pytest.raises(
                TemplateInheritanceError,
                match="Circular template inheritance",
            ):
                render_template(loaded)

    def test_a_b_a_cycle_raises(
        self,
        tmp_path: Path,
    ) -> None:
        """A→B→A cycle raises TemplateInheritanceError."""
        a_yaml = """\
template:
  name: "Template A"
  description: "test"
  version: "1.0.0"
  min_agents: 1
  max_agents: 10
  extends: "template_b"

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      level: "mid"
      model: "medium"
      department: "engineering"
"""
        b_yaml = """\
template:
  name: "Template B"
  description: "test"
  version: "1.0.0"
  min_agents: 1
  max_agents: 10
  extends: "template_a"

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      level: "mid"
      model: "medium"
      department: "engineering"
"""
        (tmp_path / "template_a.yaml").write_text(a_yaml, encoding="utf-8")
        (tmp_path / "template_b.yaml").write_text(b_yaml, encoding="utf-8")

        with patch(
            "ai_company.templates.loader._USER_TEMPLATES_DIR",
            tmp_path,
        ):
            loaded = load_template("template_a")
            with pytest.raises(
                TemplateInheritanceError,
                match="Circular template inheritance",
            ):
                render_template(loaded)

    def test_depth_limit_exceeded_raises(
        self,
        tmp_path: Path,
    ) -> None:
        """Exceeding max depth raises TemplateInheritanceError."""
        # Create a chain of 12 templates: t0 → t1 → ... → t11
        for i in range(12):
            parent_ref = f"chain_{i - 1}" if i > 0 else ""
            extends_line = f'  extends: "{parent_ref}"' if i > 0 else ""
            yaml_content = f"""\
template:
  name: "Chain {i}"
  description: "test"
  version: "1.0.0"
  min_agents: 1
  max_agents: 100
{extends_line}

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      level: "mid"
      model: "medium"
      department: "engineering"
"""
            (tmp_path / f"chain_{i}.yaml").write_text(yaml_content, encoding="utf-8")

        with patch(
            "ai_company.templates.loader._USER_TEMPLATES_DIR",
            tmp_path,
        ):
            loaded = load_template("chain_11")
            with pytest.raises(
                TemplateInheritanceError,
                match="depth exceeded",
            ):
                render_template(loaded)


# ── TestInheritanceIntegration ───────────────────────────────────


@pytest.mark.unit
class TestInheritanceIntegration:
    def test_child_extends_builtin_renders_to_valid_root_config(
        self,
        tmp_path: Path,
    ) -> None:
        """File-based child extending a builtin renders to valid RootConfig."""
        child_path = tmp_path / "custom.yaml"
        child_path.write_text(CHILD_EXTENDS_STARTUP_YAML, encoding="utf-8")
        loaded = load_template_file(child_path)
        config = render_template(loaded)
        assert isinstance(config, RootConfig)
        assert len(config.agents) >= 1
        assert len(config.departments) >= 1
