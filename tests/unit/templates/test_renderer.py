"""Tests for the two-pass template rendering pipeline."""

from typing import TYPE_CHECKING

import pytest
import structlog
from pydantic import ValidationError

from synthorg.config.schema import RootConfig
from synthorg.observability.events.template import (
    TEMPLATE_RENDER_START,
    TEMPLATE_RENDER_SUCCESS,
)
from synthorg.templates.errors import TemplateRenderError
from synthorg.templates.loader import (
    BUILTIN_TEMPLATES,
    load_template,
    load_template_file,
)
from synthorg.templates.renderer import render_template

from .conftest import TEMPLATE_REQUIRED_VAR_YAML, TEMPLATE_WITH_VARIABLES_YAML

if TYPE_CHECKING:
    from .conftest import TemplateFileFactory

pytestmark = pytest.mark.timeout(30)

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
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(TEMPLATE_WITH_VARIABLES_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded)
        assert config.company_name == "Default Corp"

    def test_user_variables_override_defaults(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(TEMPLATE_WITH_VARIABLES_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded, variables={"company_name": "Acme Inc"})
        assert config.company_name == "Acme Inc"

    def test_budget_variable_applied(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(TEMPLATE_WITH_VARIABLES_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded, variables={"budget": 100.0})
        assert config.config.budget_monthly == 100.0

    def test_required_variable_missing_raises_error(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(TEMPLATE_REQUIRED_VAR_YAML)
        loaded = load_template_file(path)
        with pytest.raises(TemplateRenderError, match="Required template variable"):
            render_template(loaded)

    def test_required_variable_provided(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(TEMPLATE_REQUIRED_VAR_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded, variables={"team_lead": "Alice"})
        assert isinstance(config, RootConfig)

    def test_extra_variables_passed_through(
        self,
        tmp_template_file: TemplateFileFactory,
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
        tmp_template_file: TemplateFileFactory,
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
      model: "medium"
      department: "engineering"
"""
        path = tmp_template_file(bad_yaml)
        loaded = load_template_file(path)
        with pytest.raises(TemplateRenderError, match="Jinja2 rendering failed"):
            render_template(loaded)


# ── Logging tests ─────────────────────────────────────────────────


@pytest.mark.unit
class TestRendererLogging:
    def test_render_emits_start_and_success(self) -> None:
        loaded = load_template("solo_founder")
        with structlog.testing.capture_logs() as cap:
            render_template(loaded)
        starts = [e for e in cap if e.get("event") == TEMPLATE_RENDER_START]
        successes = [e for e in cap if e.get("event") == TEMPLATE_RENDER_SUCCESS]
        assert len(starts) == 1
        assert len(successes) == 1


# ── _parse_rendered_yaml edge cases ──────────────────────────────


@pytest.mark.unit
class TestParseRenderedYaml:
    def test_non_dict_top_level_raises(self) -> None:
        from synthorg.templates.renderer import _parse_rendered_yaml

        with pytest.raises(TemplateRenderError, match="missing 'template' key"):
            _parse_rendered_yaml("- item1\n- item2\n", "test-source")

    def test_missing_template_key_raises(self) -> None:
        from synthorg.templates.renderer import _parse_rendered_yaml

        with pytest.raises(TemplateRenderError, match="missing 'template' key"):
            _parse_rendered_yaml("foo: bar\n", "test-source")

    def test_template_value_not_dict_raises(self) -> None:
        from synthorg.templates.renderer import _parse_rendered_yaml

        with pytest.raises(
            TemplateRenderError, match="'template' key must be a mapping"
        ):
            _parse_rendered_yaml("template: just-a-string\n", "test-source")


# ── _build_departments edge cases ────────────────────────────────


@pytest.mark.unit
class TestBuildDepartments:
    def test_invalid_budget_percent_raises(self) -> None:
        from synthorg.templates.renderer import _build_departments

        with pytest.raises(TemplateRenderError, match="Invalid department budget"):
            _build_departments(
                [{"name": "eng", "budget_percent": "not-a-number"}],
            )


# ── _validate_as_root_config edge cases ──────────────────────────


@pytest.mark.unit
class TestValidateAsRootConfig:
    def test_validation_error_raises_template_validation_error(self) -> None:
        from synthorg.templates.errors import TemplateValidationError
        from synthorg.templates.renderer import _validate_as_root_config

        with pytest.raises(
            TemplateValidationError, match="failed RootConfig validation"
        ):
            _validate_as_root_config({"company_name": 123}, "test-source")


# ── _collect_variables edge cases ────────────────────────────────


@pytest.mark.unit
class TestCollectVariables:
    def test_extra_user_vars_passed_through(self) -> None:
        from synthorg.core.enums import CompanyType
        from synthorg.templates.renderer import _collect_variables
        from synthorg.templates.schema import (
            CompanyTemplate,
            TemplateAgentConfig,
            TemplateMetadata,
        )

        template = CompanyTemplate(
            metadata=TemplateMetadata(
                name="Test",
                description="desc",
                version="1.0.0",
                company_type=CompanyType.CUSTOM,
            ),
            agents=(TemplateAgentConfig(role="Dev"),),
        )
        result = _collect_variables(template, {"undeclared_key": "value123"})
        assert result["undeclared_key"] == "value123"


# ── Inline personality and department extensions ──────────────────


@pytest.mark.unit
class TestInlinePersonality:
    def test_inline_personality_applied(self) -> None:
        """Inline personality dict is applied to agent config."""
        from synthorg.templates.renderer import _expand_single_agent

        agent: dict[str, object] = {
            "role": "Dev",
            "personality": {
                "traits": ("custom-trait",),
                "communication_style": "custom",
            },
        }
        result = _expand_single_agent(agent, 0, set(), has_extends=False)
        assert result["personality"]["communication_style"] == "custom"
        assert "custom-trait" in result["personality"]["traits"]


@pytest.mark.unit
class TestDepartmentPassthrough:
    def test_reporting_lines_passthrough(self) -> None:
        """Reporting lines from rendered data pass through to department dict."""
        from synthorg.templates.renderer import _build_departments

        raw = [
            {
                "name": "eng",
                "head_role": "cto",
                "budget_percent": 50,
                "reporting_lines": [
                    {"subordinate": "dev", "supervisor": "lead"},
                ],
            },
        ]
        result = _build_departments(raw)
        assert "reporting_lines" in result[0]
        assert len(result[0]["reporting_lines"]) == 1

    def test_policies_passthrough(self) -> None:
        """Policies from rendered data pass through to department dict."""
        from synthorg.templates.renderer import _build_departments

        raw = [
            {
                "name": "eng",
                "head_role": "cto",
                "budget_percent": 50,
                "policies": {
                    "review_requirements": {"min_reviewers": 2},
                },
            },
        ]
        result = _build_departments(raw)
        assert "policies" in result[0]

    def test_workflow_handoffs_passthrough(self) -> None:
        """Workflow handoffs pass through to config dict."""
        from synthorg.core.enums import CompanyType
        from synthorg.templates.renderer import _build_config_dict
        from synthorg.templates.schema import (
            CompanyTemplate,
            TemplateAgentConfig,
            TemplateMetadata,
        )

        template = CompanyTemplate(
            metadata=TemplateMetadata(
                name="Test",
                company_type=CompanyType.CUSTOM,
            ),
            agents=(TemplateAgentConfig(role="Dev"),),
        )
        rendered = {
            "company": {"type": "custom"},
            "agents": [{"role": "Dev"}],
            "departments": [],
            "workflow_handoffs": [
                {"from_department": "eng", "to_department": "qa", "trigger": "done"},
            ],
        }
        result = _build_config_dict(rendered, template, {})
        assert "workflow_handoffs" in result
        assert len(result["workflow_handoffs"]) == 1


@pytest.mark.unit
class TestInlinePersonalityRejection:
    def test_invalid_inline_personality_raises_template_render_error(self) -> None:
        """Invalid inline personality dict raises TemplateRenderError."""
        from synthorg.templates.renderer import _expand_single_agent

        agent: dict[str, object] = {
            "role": "Dev",
            "personality": {"openness": 99.0},
        }
        with pytest.raises(TemplateRenderError, match="Invalid inline personality"):
            _expand_single_agent(agent, 0, set(), has_extends=False)

    def test_non_dict_personality_raises_template_render_error(self) -> None:
        """Non-dict personality value raises TemplateRenderError."""
        from synthorg.templates.renderer import _expand_single_agent

        agent: dict[str, object] = {
            "role": "Dev",
            "personality": "not-a-dict",
        }
        with pytest.raises(TemplateRenderError, match="must be a mapping"):
            _expand_single_agent(agent, 0, set(), has_extends=False)


@pytest.mark.unit
class TestMissingRoleError:
    def test_missing_role_raises_template_render_error(self) -> None:
        """Agent without a 'role' field raises TemplateRenderError."""
        from synthorg.templates.renderer import _expand_single_agent

        with pytest.raises(TemplateRenderError, match="missing required 'role'"):
            _expand_single_agent({}, 0, set(), has_extends=False)


@pytest.mark.unit
class TestBuildDepartmentsTypeValidation:
    def test_non_list_reporting_lines_raises(self) -> None:
        """Non-list reporting_lines raises TemplateRenderError."""
        from synthorg.templates.renderer import _build_departments

        with pytest.raises(TemplateRenderError, match="must be a list"):
            _build_departments(
                [{"name": "eng", "reporting_lines": "not-a-list"}],
            )

    def test_non_dict_policies_raises(self) -> None:
        """Non-dict policies raises TemplateRenderError."""
        from synthorg.templates.renderer import _build_departments

        with pytest.raises(TemplateRenderError, match="must be a mapping"):
            _build_departments(
                [{"name": "eng", "policies": ["not-a-dict"]}],
            )


@pytest.mark.unit
class TestEscalationPathsPassthrough:
    def test_escalation_paths_included_in_config_dict(self) -> None:
        """Escalation paths pass through to config dict."""
        from synthorg.core.enums import CompanyType
        from synthorg.templates.renderer import _build_config_dict
        from synthorg.templates.schema import (
            CompanyTemplate,
            TemplateAgentConfig,
            TemplateMetadata,
        )

        template = CompanyTemplate(
            metadata=TemplateMetadata(
                name="Test",
                company_type=CompanyType.CUSTOM,
            ),
            agents=(TemplateAgentConfig(role="Dev"),),
        )
        rendered = {
            "company": {"type": "custom"},
            "agents": [{"role": "Dev"}],
            "departments": [],
            "escalation_paths": [
                {
                    "from_department": "eng",
                    "to_department": "qa",
                    "condition": "critical bug",
                },
            ],
        }
        result = _build_config_dict(rendered, template, {})
        assert "escalation_paths" in result
        assert len(result["escalation_paths"]) == 1


@pytest.mark.unit
class TestUnknownPresetError:
    def test_unknown_preset_raises_template_render_error(self) -> None:
        """Unknown personality_preset raises TemplateRenderError."""
        from synthorg.templates.errors import TemplateRenderError
        from synthorg.templates.renderer import _expand_single_agent

        agent: dict[str, object] = {
            "role": "Dev",
            "personality_preset": "does_not_exist",
        }
        with pytest.raises(TemplateRenderError, match="Unknown personality preset"):
            _expand_single_agent(agent, 0, set(), has_extends=False)


@pytest.mark.unit
class TestValidateListErrors:
    def test_non_list_raises(self) -> None:
        """Non-list value for a list field raises TemplateRenderError."""
        from synthorg.templates.errors import TemplateRenderError
        from synthorg.templates.renderer import _validate_list

        with pytest.raises(TemplateRenderError, match="must be a list"):
            _validate_list({"agents": "not-a-list"}, "agents")

    def test_non_dict_item_raises(self) -> None:
        """Non-dict item in a list field raises TemplateRenderError."""
        from synthorg.templates.errors import TemplateRenderError
        from synthorg.templates.renderer import _validate_list

        with pytest.raises(TemplateRenderError, match="must be a mapping"):
            _validate_list({"agents": [{"role": "Dev"}, "bad"]}, "agents")


# ── Roster count tests ──────────────────────────────────────────


@pytest.mark.unit
class TestExpandPreservesMergeId:
    def test_expand_preserves_merge_id(self) -> None:
        """Expanded agent dict contains merge_id when set."""
        from synthorg.templates.renderer import _expand_single_agent

        agent: dict[str, object] = {
            "role": "Full-Stack Developer",
            "merge_id": "frontend",
            "department": "engineering",
        }
        result = _expand_single_agent(agent, 0, set(), has_extends=True)
        assert result.get("merge_id") == "frontend"

    def test_expand_omits_empty_merge_id(self) -> None:
        """Expanded agent dict omits merge_id when empty."""
        from synthorg.templates.renderer import _expand_single_agent

        agent: dict[str, object] = {
            "role": "Full-Stack Developer",
            "merge_id": "",
            "department": "engineering",
        }
        result = _expand_single_agent(agent, 0, set(), has_extends=True)
        assert "merge_id" not in result

    def test_expand_omits_merge_id_without_extends(self) -> None:
        """Standalone templates do not leak merge_id into output."""
        from synthorg.templates.renderer import _expand_single_agent

        agent: dict[str, object] = {
            "role": "Full-Stack Developer",
            "merge_id": "frontend",
            "department": "engineering",
        }
        result = _expand_single_agent(agent, 0, set(), has_extends=False)
        assert "merge_id" not in result


@pytest.mark.unit
class TestRosterCounts:
    @pytest.mark.parametrize("name", sorted(BUILTIN_TEMPLATES))
    def test_template_agent_count_in_range(self, name: str) -> None:
        """Each template renders agents within its declared metadata range."""
        loaded = load_template(name)
        config = render_template(loaded)
        lo = loaded.template.metadata.min_agents
        hi = loaded.template.metadata.max_agents
        assert lo <= len(config.agents) <= hi, (
            f"{name}: expected {lo}-{hi} agents, got {len(config.agents)}"
        )
        assert isinstance(config, RootConfig)

    def test_full_company_variable_override(self) -> None:
        """full_company num_backend_devs override changes agent count."""
        loaded = load_template("full_company")
        default_config = render_template(loaded)
        override_config = render_template(
            loaded,
            variables={"num_backend_devs": 5},
        )
        # Default is 3 backend devs, override is 5 → +2 agents.
        default_backend = sum(
            1 for a in default_config.agents if a.role == "Backend Developer"
        )
        override_backend = sum(
            1 for a in override_config.agents if a.role == "Backend Developer"
        )
        assert default_backend == 3
        assert override_backend == 5
        assert override_backend - default_backend == 2
