# mypy: disable-error-code="explicit-any"
"""Tests for workflow configuration integration in templates.

Verifies that templates can declare ``workflow_config`` sections with
Kanban/Sprint sub-configurations, and that the renderer produces valid
``WorkflowConfig`` objects on the rendered ``RootConfig``.
"""

from typing import TYPE_CHECKING, ClassVar

import pytest

from synthorg.engine.workflow.ceremony_policy import CeremonyStrategyType
from synthorg.engine.workflow.config import WorkflowConfig
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.kanban_board import KanbanConfig
from synthorg.engine.workflow.kanban_columns import KanbanColumn
from synthorg.engine.workflow.sprint_config import SprintConfig
from synthorg.engine.workflow.velocity_types import VelocityCalcType
from synthorg.templates.loader import (
    BUILTIN_TEMPLATES,
    load_template,
    load_template_file,
)
from synthorg.templates.renderer import render_template
from synthorg.templates.schema import CompanyTemplate

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from .conftest import TemplateFileFactory


# ── Schema: CompanyTemplate accepts workflow_config ─────────────


@pytest.mark.unit
class TestCompanyTemplateWorkflowConfig:
    """CompanyTemplate must accept an optional workflow_config dict."""

    def test_default_workflow_config_is_empty_dict(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        t = CompanyTemplate(**make_template_dict())
        assert t.workflow_config == {}

    def test_accepts_kanban_config(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        wf_config = {
            "kanban": {
                "wip_limits": [
                    {"column": "in_progress", "limit": 3},
                ],
                "enforce_wip": True,
            },
        }
        t = CompanyTemplate(**make_template_dict(workflow_config=wf_config))
        wfc: Any = t.workflow_config
        assert wfc["kanban"]["enforce_wip"] is True

    def test_accepts_sprint_config(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        wf_config = {
            "sprint": {
                "duration_days": 7,
                "max_tasks_per_sprint": 20,
            },
        }
        t = CompanyTemplate(**make_template_dict(workflow_config=wf_config))
        wfc: Any = t.workflow_config
        assert wfc["sprint"]["duration_days"] == 7

    def test_accepts_full_agile_kanban_config(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        wf_config = {
            "kanban": {
                "wip_limits": [
                    {"column": "in_progress", "limit": 5},
                    {"column": "review", "limit": 3},
                ],
            },
            "sprint": {
                "duration_days": 14,
                "ceremonies": [
                    {
                        "name": "sprint_planning",
                        "protocol": "structured_phases",
                        "frequency": "bi_weekly",
                    },
                ],
            },
        }
        t = CompanyTemplate(
            **make_template_dict(
                workflow="agile_kanban",
                workflow_config=wf_config,
            ),
        )
        assert "kanban" in t.workflow_config
        assert "sprint" in t.workflow_config

    def test_jinja2_placeholder_survives_pass1(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        """Pass 1 strips Jinja2 to __JINJA2__, so workflow_config may
        contain placeholder strings.  CompanyTemplate must accept them."""
        wf_config = {
            "kanban": {
                "wip_limits": [
                    {"column": "in_progress", "limit": "__JINJA2__"},
                ],
            },
        }
        t = CompanyTemplate(**make_template_dict(workflow_config=wf_config))
        wfc: Any = t.workflow_config
        assert wfc["kanban"]["wip_limits"][0]["limit"] == "__JINJA2__"


# ── Renderer: workflow flows through to RootConfig ──────────────


KANBAN_TEMPLATE_YAML = """\
template:
  name: "Kanban Test"
  description: "Template with kanban workflow config"
  version: "1.0.0"

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      name: "Test Dev"
      level: "mid"
      model: "medium"
      department: "engineering"

  workflow: "kanban"
  communication: "event_driven"

  workflow_config:
    kanban:
      wip_limits:
        - column: "in_progress"
          limit: 2
        - column: "review"
          limit: 1
      enforce_wip: true
"""

AGILE_KANBAN_TEMPLATE_YAML = """\
template:
  name: "Agile Test"
  description: "Template with agile_kanban workflow config"
  version: "1.0.0"

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      name: "Test Dev"
      level: "mid"
      model: "medium"
      department: "engineering"

  workflow: "agile_kanban"
  communication: "hybrid"

  workflow_config:
    kanban:
      wip_limits:
        - column: "in_progress"
          limit: 3
      enforce_wip: true
    sprint:
      duration_days: 7
      max_tasks_per_sprint: 20
      ceremonies:
        - name: "sprint_planning"
          protocol: "structured_phases"
          frequency: "weekly"
        - name: "sprint_review"
          protocol: "round_robin"
          frequency: "weekly"
"""

VARIABLE_WORKFLOW_TEMPLATE_YAML = """\
template:
  name: "Variable WF Test"
  description: "Template with workflow variables"
  version: "1.0.0"

  variables:
    - name: "company_name"
      description: "Name"
      default: "Test Co"
    - name: "sprint_length"
      description: "Sprint duration in days"
      var_type: "int"
      default: 14
    - name: "wip_limit"
      description: "WIP limit"
      var_type: "int"
      default: 4

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      name: "Test Dev"
      level: "mid"
      model: "medium"
      department: "engineering"

  workflow: "agile_kanban"
  communication: "hybrid"

  workflow_config:
    kanban:
      wip_limits:
        - column: "in_progress"
          limit: {{ wip_limit | default(4) }}
      enforce_wip: true
    sprint:
      duration_days: {{ sprint_length | default(14) }}
"""


@pytest.mark.unit
class TestRendererWorkflowConfig:
    """Renderer must build a WorkflowConfig-compatible dict from template data."""

    def test_kanban_only_template(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(KANBAN_TEMPLATE_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded)
        assert config.workflow.workflow_type == WorkflowType.KANBAN
        assert config.workflow.kanban.enforce_wip is True
        limits = {wl.column: wl.limit for wl in config.workflow.kanban.wip_limits}
        assert limits[KanbanColumn.IN_PROGRESS] == 2
        assert limits[KanbanColumn.REVIEW] == 1

    def test_agile_kanban_template(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(AGILE_KANBAN_TEMPLATE_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded)
        assert config.workflow.workflow_type == WorkflowType.AGILE_KANBAN
        assert config.workflow.sprint.duration_days == 7
        assert config.workflow.sprint.max_tasks_per_sprint == 20
        ceremony_names = {c.name for c in config.workflow.sprint.ceremonies}
        assert ceremony_names == {"sprint_planning", "sprint_review"}

    def test_backward_compat_no_workflow_config(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        """Templates without workflow_config still render to valid RootConfig."""
        from .conftest import MINIMAL_TEMPLATE_YAML

        path = tmp_template_file(MINIMAL_TEMPLATE_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded)
        assert isinstance(config.workflow, WorkflowConfig)
        # Default workflow type is agile_kanban.
        assert config.workflow.workflow_type == WorkflowType.AGILE_KANBAN

    def test_workflow_type_without_config_passes_through(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        """A template that sets workflow: kanban but no workflow_config
        should produce WorkflowConfig with KANBAN type and defaults."""
        yaml_text = """\
template:
  name: "Type Only"
  description: "Just sets workflow type"
  version: "1.0.0"
  company:
    type: "custom"
  agents:
    - role: "Backend Developer"
      name: "Dev"
      level: "mid"
      model: "medium"
      department: "engineering"
  workflow: "kanban"
  communication: "event_driven"
"""
        path = tmp_template_file(yaml_text)
        loaded = load_template_file(path)
        config = render_template(loaded)
        assert config.workflow.workflow_type == WorkflowType.KANBAN
        # Sub-configs should be defaults.
        assert config.workflow.kanban == KanbanConfig()
        assert config.workflow.sprint == SprintConfig()

    def test_unknown_keys_in_workflow_config_dropped_with_warning(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        """Unknown keys in workflow_config are dropped with a warning log;
        only kanban/sprint are forwarded to WorkflowConfig."""
        import structlog

        from synthorg.observability.events.template import (
            TEMPLATE_WORKFLOW_CONFIG_UNKNOWN_KEY,
        )

        yaml_text = """\
template:
  name: "Unknown Keys"
  description: "Has unknown workflow_config keys"
  version: "1.0.0"
  company:
    type: "custom"
  agents:
    - role: "Backend Developer"
      name: "Dev"
      level: "mid"
      model: "medium"
      department: "engineering"
  workflow: "kanban"
  communication: "event_driven"
  workflow_config:
    scrumban:
      duration_days: 5
    kanban:
      enforce_wip: false
"""
        path = tmp_template_file(yaml_text)
        loaded = load_template_file(path)
        with structlog.testing.capture_logs() as cap:
            config = render_template(loaded)
        # scrumban key is dropped.
        assert config.workflow.kanban.enforce_wip is False
        # Warning emitted for the unknown key.
        warnings = [
            e for e in cap if e.get("event") == TEMPLATE_WORKFLOW_CONFIG_UNKNOWN_KEY
        ]
        assert len(warnings) == 1
        assert "scrumban" in warnings[0]["unknown_keys"]

    def test_invalid_workflow_config_raises_on_render(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        """Structurally invalid workflow_config passes Pass 1 but fails
        validation during render_template when WorkflowConfig is built."""
        from synthorg.templates.errors import TemplateValidationError

        yaml_text = """\
template:
  name: "Invalid WF"
  description: "Invalid workflow_config content"
  version: "1.0.0"
  company:
    type: "custom"
  agents:
    - role: "Backend Developer"
      name: "Dev"
      level: "mid"
      model: "medium"
      department: "engineering"
  workflow: "kanban"
  communication: "event_driven"
  workflow_config:
    kanban:
      wip_limits:
        - column: "invalid_column_name"
          limit: 3
"""
        path = tmp_template_file(yaml_text)
        loaded = load_template_file(path)
        with pytest.raises(TemplateValidationError):
            render_template(loaded)


# ── Template variables in workflow_config ───────────────────────


@pytest.mark.unit
class TestWorkflowConfigVariables:
    """Template variables (sprint_length, wip_limit) must be substituted
    into workflow_config during Jinja2 rendering."""

    def test_defaults_applied(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(VARIABLE_WORKFLOW_TEMPLATE_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded)
        assert config.workflow.sprint.duration_days == 14
        limits = {wl.column: wl.limit for wl in config.workflow.kanban.wip_limits}
        assert limits[KanbanColumn.IN_PROGRESS] == 4

    def test_user_overrides_applied(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(VARIABLE_WORKFLOW_TEMPLATE_YAML)
        loaded = load_template_file(path)
        config = render_template(
            loaded,
            variables={"sprint_length": 7, "wip_limit": 2},
        )
        assert config.workflow.sprint.duration_days == 7
        limits = {wl.column: wl.limit for wl in config.workflow.kanban.wip_limits}
        assert limits[KanbanColumn.IN_PROGRESS] == 2


# ── Builtin templates produce correct WorkflowConfig ────────────


@pytest.mark.unit
class TestBuiltinWorkflowConfigs:
    """All builtin templates must render to valid RootConfig with the
    correct WorkflowConfig matching their declared workflow type."""

    # (name, expected_workflow_type)
    _EXPECTED_TYPES: ClassVar[list[tuple[str, str]]] = [
        ("solo_founder", "kanban"),
        ("startup", "agile_kanban"),
        ("dev_shop", "agile_kanban"),
        ("product_team", "agile_kanban"),
        ("agency", "kanban"),
        ("research_lab", "kanban"),
        ("full_company", "agile_kanban"),
        ("consultancy", "kanban"),
        ("data_team", "kanban"),
    ]

    def test_matrix_covers_all_builtins(self) -> None:
        tested = {row[0] for row in self._EXPECTED_TYPES}
        assert tested == set(BUILTIN_TEMPLATES)

    def test_builtin_registry_matches_filesystem(self) -> None:
        """BUILTIN_TEMPLATES must list every .yaml in the builtins dir."""
        from importlib import resources

        builtins_dir = resources.files("synthorg.templates.builtins")
        on_disk = {
            p.name.removesuffix(".yaml")
            for p in builtins_dir.iterdir()
            if str(p).endswith(".yaml")
        }
        assert set(BUILTIN_TEMPLATES) == on_disk

    @pytest.mark.parametrize(
        ("name", "expected_type"),
        _EXPECTED_TYPES,
        ids=[row[0] for row in _EXPECTED_TYPES],
    )
    def test_workflow_type_matches(self, name: str, expected_type: str) -> None:
        loaded = load_template(name)
        config = render_template(loaded)
        assert config.workflow.workflow_type == WorkflowType(expected_type)

    @pytest.mark.parametrize(
        "name",
        list(BUILTIN_TEMPLATES),
        ids=list(BUILTIN_TEMPLATES),
    )
    def test_workflow_config_valid(self, name: str) -> None:
        """Every builtin must declare workflow_config with no unknown keys."""
        import structlog

        from synthorg.observability.events.template import (
            TEMPLATE_WORKFLOW_CONFIG_UNKNOWN_KEY,
        )

        loaded = load_template(name)
        # Builtins must explicitly declare workflow_config, not rely on defaults.
        assert loaded.template.workflow_config, f"{name}: missing workflow_config"
        with structlog.testing.capture_logs() as cap:
            config = render_template(loaded)
        assert isinstance(config.workflow, WorkflowConfig)
        unknown_warnings = [
            e for e in cap if e.get("event") == TEMPLATE_WORKFLOW_CONFIG_UNKNOWN_KEY
        ]
        assert not unknown_warnings, (
            f"{name}: unknown workflow_config keys: {unknown_warnings}"
        )

    @pytest.mark.parametrize(
        "name",
        ["startup", "dev_shop", "product_team", "full_company"],
        ids=["startup", "dev_shop", "product_team", "full_company"],
    )
    def test_agile_templates_have_ceremonies(self, name: str) -> None:
        """Agile kanban templates must explicitly declare sprint ceremonies."""
        loaded = load_template(name)
        # Verify the template YAML explicitly declares sprint ceremonies.
        sprint_cfg = loaded.template.workflow_config.get("sprint", {})
        assert isinstance(sprint_cfg, dict)
        assert sprint_cfg.get("ceremonies"), f"{name}: missing sprint ceremonies"
        config = render_template(loaded)
        assert len(config.workflow.sprint.ceremonies) >= 1

    @pytest.mark.parametrize(
        "name",
        ["solo_founder", "research_lab", "data_team"],
        ids=["solo_founder", "research_lab", "data_team"],
    )
    def test_advisory_wip_templates(self, name: str) -> None:
        """Templates with full autonomy use advisory WIP enforcement."""
        loaded = load_template(name)
        config = render_template(loaded)
        assert config.workflow.kanban.enforce_wip is False

    @pytest.mark.parametrize(
        "name",
        ["agency", "consultancy"],
        ids=["agency", "consultancy"],
    )
    def test_strict_kanban_templates(self, name: str) -> None:
        """Supervised kanban templates use strict WIP enforcement."""
        loaded = load_template(name)
        config = render_template(loaded)
        assert config.workflow.kanban.enforce_wip is True

    @pytest.mark.parametrize(
        "name",
        ["startup", "dev_shop", "product_team", "full_company"],
        ids=["startup", "dev_shop", "product_team", "full_company"],
    )
    def test_agile_templates_enforce_wip(self, name: str) -> None:
        """Agile kanban templates use strict WIP enforcement."""
        loaded = load_template(name)
        config = render_template(loaded)
        assert config.workflow.kanban.enforce_wip is True

    # (name, expected_in_progress_wip, expected_sprint_days)
    _EXPECTED_DEFAULTS: ClassVar[list[tuple[str, int, int]]] = [
        ("solo_founder", 3, 14),
        ("startup", 3, 7),
        ("dev_shop", 2, 14),
        ("product_team", 3, 14),
        ("agency", 3, 14),
        ("research_lab", 3, 14),
        ("full_company", 5, 14),
        ("consultancy", 2, 14),
        ("data_team", 3, 14),
    ]

    @pytest.mark.parametrize(
        ("name", "expected_wip", "expected_sprint_days"),
        _EXPECTED_DEFAULTS,
        ids=[row[0] for row in _EXPECTED_DEFAULTS],
    )
    def test_builtin_default_values(
        self,
        name: str,
        expected_wip: int,
        expected_sprint_days: int,
    ) -> None:
        """Builtin templates must render with their documented default values."""
        loaded = load_template(name)
        config = render_template(loaded)
        limits = {wl.column: wl.limit for wl in config.workflow.kanban.wip_limits}
        assert limits[KanbanColumn.IN_PROGRESS] == expected_wip
        assert config.workflow.sprint.duration_days == expected_sprint_days

    # (name, expected_ceremony_strategy)
    _EXPECTED_STRATEGIES: ClassVar[list[tuple[str, str]]] = [
        ("solo_founder", "task_driven"),
        ("startup", "task_driven"),
        ("dev_shop", "hybrid"),
        ("product_team", "hybrid"),
        ("agency", "event_driven"),
        ("full_company", "hybrid"),
        ("research_lab", "throughput_adaptive"),
        ("consultancy", "calendar"),
        ("data_team", "task_driven"),
    ]

    def test_strategy_matrix_covers_all_builtins(self) -> None:
        tested = {row[0] for row in self._EXPECTED_STRATEGIES}
        assert tested == set(BUILTIN_TEMPLATES)

    @pytest.mark.parametrize(
        ("name", "expected_strategy"),
        _EXPECTED_STRATEGIES,
        ids=[row[0] for row in _EXPECTED_STRATEGIES],
    )
    def test_builtin_ceremony_policy_strategy(
        self,
        name: str,
        expected_strategy: str,
    ) -> None:
        """Each builtin template must declare its default ceremony strategy."""
        loaded = load_template(name)
        config = render_template(loaded)
        policy = config.workflow.sprint.ceremony_policy
        assert policy.strategy == CeremonyStrategyType(expected_strategy)

    # (name, expected_velocity_calculator)
    _EXPECTED_VELOCITY_CALCS: ClassVar[list[tuple[str, str]]] = [
        ("solo_founder", "task_driven"),
        ("startup", "task_driven"),
        ("dev_shop", "multi_dimensional"),
        ("product_team", "multi_dimensional"),
        ("agency", "points_per_sprint"),
        ("full_company", "multi_dimensional"),
        ("research_lab", "task_driven"),
        ("consultancy", "calendar"),
        ("data_team", "task_driven"),
    ]

    def test_velocity_calc_matrix_covers_all_builtins(self) -> None:
        tested = {row[0] for row in self._EXPECTED_VELOCITY_CALCS}
        assert tested == set(BUILTIN_TEMPLATES)

    @pytest.mark.parametrize(
        ("name", "expected_calc"),
        _EXPECTED_VELOCITY_CALCS,
        ids=[row[0] for row in _EXPECTED_VELOCITY_CALCS],
    )
    def test_builtin_ceremony_policy_velocity_calculator(
        self,
        name: str,
        expected_calc: str,
    ) -> None:
        """Each builtin template must declare its default velocity calculator."""
        loaded = load_template(name)
        # Assert raw template YAML declares the field (not just derived)
        sprint_cfg = loaded.template.workflow_config.get("sprint", {})
        assert isinstance(sprint_cfg, dict)
        ceremony_policy = sprint_cfg.get("ceremony_policy", {})
        assert isinstance(ceremony_policy, dict)
        assert ceremony_policy.get("velocity_calculator") == expected_calc
        # Assert rendered config matches
        config = render_template(loaded)
        policy = config.workflow.sprint.ceremony_policy
        assert policy.velocity_calculator == VelocityCalcType(expected_calc)


# ── Department ceremony policy passthrough ───────────────────────


DEPT_CEREMONY_POLICY_TEMPLATE_YAML = """\
template:
  name: "Dept Policy Test"
  description: "Template with department ceremony policy"
  version: "1.0.0"

  company:
    type: "custom"

  agents:
    - role: "Backend Developer"
      name: "Test Dev"
      level: "mid"
      model: "medium"
      department: "engineering"
    - role: "Designer"
      name: "Test Designer"
      level: "mid"
      model: "medium"
      department: "marketing"

  departments:
    - name: "engineering"
      budget_percent: 60
      head_role: "Backend Developer"
    - name: "marketing"
      budget_percent: 40
      head_role: "Designer"
      ceremony_policy:
        strategy: "calendar"
        transition_threshold: 0.8

  workflow: "kanban"
  communication: "event_driven"

  workflow_config:
    kanban:
      wip_limits:
        - column: "in_progress"
          limit: 3
      enforce_wip: false
    sprint:
      ceremony_policy:
        strategy: "task_driven"
"""


@pytest.mark.unit
class TestDepartmentCeremonyPolicy:
    """Department-level ceremony policy override flows through rendering."""

    def test_department_ceremony_policy_flows_to_root_config(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(DEPT_CEREMONY_POLICY_TEMPLATE_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded)

        dept_by_name = {d.name: d for d in config.departments}
        marketing = dept_by_name["marketing"]
        assert marketing.ceremony_policy is not None
        assert marketing.ceremony_policy["strategy"] == "calendar"
        assert marketing.ceremony_policy["transition_threshold"] == 0.8

    def test_department_without_policy_defaults_none(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        path = tmp_template_file(DEPT_CEREMONY_POLICY_TEMPLATE_YAML)
        loaded = load_template_file(path)
        config = render_template(loaded)

        dept_by_name = {d.name: d for d in config.departments}
        engineering = dept_by_name["engineering"]
        assert engineering.ceremony_policy is None

    def test_department_ceremony_policy_non_dict_rejected(
        self,
        tmp_template_file: TemplateFileFactory,
    ) -> None:
        """Scalar ceremony_policy on a department is rejected at load time
        (Pydantic validates dict type on TemplateDepartmentConfig)."""
        from synthorg.templates.errors import TemplateValidationError

        yaml_text = """\
template:
  name: "Bad Policy"
  description: "Department with scalar ceremony_policy"
  version: "1.0.0"
  company:
    type: "custom"
  agents:
    - role: "Dev"
      name: "Dev"
      level: "mid"
      model: "medium"
      department: "engineering"
  departments:
    - name: "engineering"
      budget_percent: 100
      head_role: "Dev"
      ceremony_policy: "calendar"
  workflow: "kanban"
  communication: "event_driven"
  workflow_config:
    kanban:
      wip_limits:
        - column: "in_progress"
          limit: 3
      enforce_wip: false
"""
        path = tmp_template_file(yaml_text)
        with pytest.raises(TemplateValidationError, match="ceremony_policy"):
            load_template_file(path)
