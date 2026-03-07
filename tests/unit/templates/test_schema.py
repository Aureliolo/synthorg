"""Tests for template schema models."""

from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from ai_company.core.enums import CompanyType, SeniorityLevel
from ai_company.templates.schema import (
    CompanyTemplate,
    TemplateAgentConfig,
    TemplateDepartmentConfig,
    TemplateMetadata,
    TemplateVariable,
)

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.timeout(30)

# ── TemplateVariable ─────────────────────────────────────────────


@pytest.mark.unit
class TestTemplateVariable:
    def test_valid_minimal(self) -> None:
        v = TemplateVariable(name="my_var")
        assert v.name == "my_var"
        assert v.description == ""
        assert v.var_type == "str"
        assert v.default is None
        assert v.required is False

    def test_valid_full(self) -> None:
        v = TemplateVariable(
            name="budget",
            description="Monthly budget",
            var_type="float",
            default=50.0,
            required=False,
        )
        assert v.var_type == "float"
        assert v.default == 50.0

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateVariable(name="")

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateVariable(name="   ")

    def test_required_with_default_rejected(self) -> None:
        with pytest.raises(ValidationError, match="required but defines a default"):
            TemplateVariable(name="x", required=True, default="oops")

    def test_required_without_default_accepted(self) -> None:
        v = TemplateVariable(name="x", required=True)
        assert v.required is True
        assert v.default is None

    def test_frozen(self) -> None:
        v = TemplateVariable(name="x")
        with pytest.raises(ValidationError):
            v.name = "y"  # type: ignore[misc]


# ── TemplateAgentConfig ──────────────────────────────────────────


@pytest.mark.unit
class TestTemplateAgentConfig:
    def test_valid_minimal(self) -> None:
        a = TemplateAgentConfig(role="Backend Developer")
        assert a.role == "Backend Developer"
        assert a.name == ""
        assert a.level == SeniorityLevel.MID
        assert a.model == "medium"
        assert a.personality_preset is None
        assert a.department is None

    def test_valid_full(self) -> None:
        a = TemplateAgentConfig(
            role="CEO",
            name="{{ company_name }} CEO",
            level=SeniorityLevel.C_SUITE,
            model="large",
            personality_preset="visionary_leader",
            department="executive",
        )
        assert a.level == SeniorityLevel.C_SUITE
        assert a.personality_preset == "visionary_leader"

    def test_blank_role_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateAgentConfig(role="")

    def test_inline_personality(self) -> None:
        a = TemplateAgentConfig(
            role="Dev",
            personality={"openness": 0.9, "traits": ("bold",)},
        )
        assert a.personality is not None
        assert a.personality["openness"] == 0.9

    def test_personality_none_by_default(self) -> None:
        a = TemplateAgentConfig(role="Dev")
        assert a.personality is None

    def test_blank_model_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateAgentConfig(role="dev", model="")

    def test_whitespace_model_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateAgentConfig(role="dev", model="   ")

    def test_both_personality_and_preset_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Cannot specify both"):
            TemplateAgentConfig(
                role="Dev",
                personality_preset="visionary_leader",
                personality={"openness": 0.9},
            )

    def test_remove_alias(self) -> None:
        a = TemplateAgentConfig(role="Dev", _remove=True)
        assert a.remove is True

    def test_remove_default_false(self) -> None:
        a = TemplateAgentConfig(role="Dev")
        assert a.remove is False


# ── TemplateDepartmentConfig ─────────────────────────────────────


@pytest.mark.unit
class TestTemplateDepartmentConfig:
    def test_valid_minimal(self) -> None:
        d = TemplateDepartmentConfig(name="engineering")
        assert d.name == "engineering"
        assert d.budget_percent == 0.0
        assert d.head_role is None

    def test_valid_full(self) -> None:
        d = TemplateDepartmentConfig(
            name="engineering",
            budget_percent=60.0,
            head_role="CTO",
        )
        assert d.budget_percent == 60.0
        assert d.head_role == "CTO"

    def test_budget_percent_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateDepartmentConfig(name="eng", budget_percent=-1.0)

    def test_budget_percent_over_100_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateDepartmentConfig(name="eng", budget_percent=101.0)

    def test_reporting_lines_default_empty(self) -> None:
        d = TemplateDepartmentConfig(name="eng")
        assert d.reporting_lines == ()

    def test_reporting_lines_accepted(self) -> None:
        d = TemplateDepartmentConfig(
            name="eng",
            reporting_lines=({"subordinate": "dev", "supervisor": "lead"},),
        )
        assert len(d.reporting_lines) == 1

    def test_policies_none_by_default(self) -> None:
        d = TemplateDepartmentConfig(name="eng")
        assert d.policies is None

    def test_policies_accepted(self) -> None:
        d = TemplateDepartmentConfig(
            name="eng",
            policies={"review_requirements": {"min_reviewers": 2}},
        )
        assert d.policies is not None

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateDepartmentConfig(name="")


# ── TemplateMetadata ─────────────────────────────────────────────


@pytest.mark.unit
class TestTemplateMetadata:
    def test_valid_minimal(self) -> None:
        m = TemplateMetadata(name="Test", company_type=CompanyType.CUSTOM)
        assert m.name == "Test"
        assert m.company_type == CompanyType.CUSTOM
        assert m.min_agents == 1
        assert m.max_agents == 100
        assert m.tags == ()

    def test_valid_full(self) -> None:
        m = TemplateMetadata(
            name="My Template",
            description="A description",
            version="2.0.0",
            company_type=CompanyType.STARTUP,
            min_agents=2,
            max_agents=10,
            tags=("startup", "mvp"),
        )
        assert m.version == "2.0.0"
        assert m.tags == ("startup", "mvp")

    def test_min_greater_than_max_rejected(self) -> None:
        with pytest.raises(ValidationError, match="min_agents"):
            TemplateMetadata(
                name="Bad",
                company_type=CompanyType.CUSTOM,
                min_agents=10,
                max_agents=5,
            )

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateMetadata(name="", company_type=CompanyType.CUSTOM)

    def test_invalid_company_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateMetadata(name="T", company_type="nonexistent_type")  # type: ignore[arg-type]


# ── CompanyTemplate ──────────────────────────────────────────────


@pytest.mark.unit
class TestCompanyTemplate:
    def test_valid_minimal(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        t = CompanyTemplate(**make_template_dict())
        assert t.metadata.name == "Test"
        assert len(t.agents) == 1
        assert t.workflow == "agile_kanban"
        assert t.communication == "hybrid"
        assert t.budget_monthly == 50.0
        assert t.autonomy == 0.5

    def test_agent_count_below_min_rejected(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        with pytest.raises(ValidationError, match="minimum"):
            CompanyTemplate(
                **make_template_dict(
                    metadata={
                        "name": "T",
                        "company_type": "custom",
                        "min_agents": 3,
                    },
                    agents=({"role": "Dev", "level": "mid"},),
                )
            )

    def test_agent_count_above_max_rejected(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        agents = tuple({"role": f"Dev{i}", "level": "mid"} for i in range(5))
        with pytest.raises(ValidationError, match="maximum"):
            CompanyTemplate(
                **make_template_dict(
                    metadata={
                        "name": "T",
                        "company_type": "custom",
                        "max_agents": 2,
                    },
                    agents=agents,
                )
            )

    def test_duplicate_variable_names_rejected(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        with pytest.raises(ValidationError, match="Duplicate variable names"):
            CompanyTemplate(
                **make_template_dict(
                    variables=(
                        {"name": "x", "var_type": "str"},
                        {"name": "x", "var_type": "int"},
                    ),
                )
            )

    def test_duplicate_department_names_rejected(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        with pytest.raises(ValidationError, match="Duplicate department names"):
            CompanyTemplate(
                **make_template_dict(
                    departments=(
                        {"name": "eng", "budget_percent": 50},
                        {"name": "eng", "budget_percent": 50},
                    ),
                )
            )

    def test_unique_variables_accepted(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        t = CompanyTemplate(
            **make_template_dict(
                variables=(
                    {"name": "x"},
                    {"name": "y"},
                ),
            )
        )
        assert len(t.variables) == 2

    def test_autonomy_out_of_range_rejected(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        with pytest.raises(ValidationError):
            CompanyTemplate(**make_template_dict(autonomy=1.5))

    def test_negative_budget_rejected(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        with pytest.raises(ValidationError):
            CompanyTemplate(**make_template_dict(budget_monthly=-10.0))

    def test_blank_workflow_rejected(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        with pytest.raises(ValidationError):
            CompanyTemplate(**make_template_dict(workflow=""))

    def test_whitespace_workflow_rejected(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        with pytest.raises(ValidationError):
            CompanyTemplate(**make_template_dict(workflow="   "))

    def test_blank_communication_rejected(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        with pytest.raises(ValidationError):
            CompanyTemplate(**make_template_dict(communication=""))

    def test_whitespace_communication_rejected(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        with pytest.raises(ValidationError):
            CompanyTemplate(**make_template_dict(communication="   "))

    def test_workflow_handoffs_default_empty(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        t = CompanyTemplate(**make_template_dict())
        assert t.workflow_handoffs == ()

    def test_escalation_paths_default_empty(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        t = CompanyTemplate(**make_template_dict())
        assert t.escalation_paths == ()

    def test_workflow_handoffs_accepted(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        t = CompanyTemplate(
            **make_template_dict(
                workflow_handoffs=(
                    {
                        "from_department": "eng",
                        "to_department": "qa",
                        "trigger": "done",
                    },
                ),
            )
        )
        assert len(t.workflow_handoffs) == 1

    def test_escalation_paths_accepted(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        t = CompanyTemplate(
            **make_template_dict(
                escalation_paths=(
                    {
                        "from_department": "eng",
                        "to_department": "security",
                        "condition": "vulnerability found",
                    },
                ),
            )
        )
        assert len(t.escalation_paths) == 1

    def test_extends_field_accepted(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        t = CompanyTemplate(**make_template_dict(extends="startup", agents=()))
        assert t.extends == "startup"

    def test_extends_normalizes_case(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        t = CompanyTemplate(**make_template_dict(extends="  StartUp  ", agents=()))
        assert t.extends == "startup"

    def test_extends_skips_agent_count_validation(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        t = CompanyTemplate(**make_template_dict(extends="startup", agents=()))
        assert len(t.agents) == 0

    def test_frozen(
        self,
        make_template_dict: Callable[..., dict[str, Any]],
    ) -> None:
        t = CompanyTemplate(**make_template_dict())
        with pytest.raises(ValidationError):
            t.workflow = "scrum"  # type: ignore[misc]
