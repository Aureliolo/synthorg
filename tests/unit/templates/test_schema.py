"""Tests for template schema models."""

import pytest
from pydantic import JsonValue, ValidationError

from synthorg.hr.seniority import SeniorityLevel
from synthorg.organization.enums import CompanyType
from synthorg.templates.enums import SkillPattern
from synthorg.templates.schema import (
    CompanyTemplate,
    TemplateAgentConfig,
    TemplateDepartmentConfig,
    TemplateMetadata,
    TemplateVariable,
)

from .conftest import TemplateDictFactory

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
        assert a.name is None
        assert a.merge_id is None
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

    @pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
    def test_blank_name_rejected(self, bad: str) -> None:
        """Empty / whitespace-only names must be rejected -- use None instead."""
        with pytest.raises(ValidationError):
            TemplateAgentConfig(role="Dev", name=bad)

    @pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
    def test_blank_merge_id_rejected(self, bad: str) -> None:
        """Empty / whitespace-only merge_id must be rejected -- use None instead."""
        with pytest.raises(ValidationError):
            TemplateAgentConfig(role="Dev", merge_id=bad)

    def test_non_blank_name_accepted(self) -> None:
        a = TemplateAgentConfig(role="Dev", name="Alice")
        assert a.name == "Alice"

    def test_non_blank_merge_id_accepted(self) -> None:
        a = TemplateAgentConfig(role="Dev", merge_id="backend-senior")
        assert a.merge_id == "backend-senior"

    def test_inline_personality(self) -> None:
        a = TemplateAgentConfig(
            role="Dev",
            personality={"openness": 0.9, "traits": ["bold"]},
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

    def test_dict_model_accepted(self) -> None:
        model_dict: dict[str, JsonValue] = {
            "tier": "large",
            "priority": "quality",
            "min_context": 100000,
        }
        a = TemplateAgentConfig(role="CEO", model=model_dict)
        assert a.model == model_dict

    def test_dict_model_tier_only(self) -> None:
        a = TemplateAgentConfig(role="Dev", model={"tier": "small"})
        assert a.model == {"tier": "small"}

    def test_dict_model_invalid_tier_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateAgentConfig(role="Dev", model={"tier": "huge"})

    def test_dict_model_invalid_priority_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateAgentConfig(
                role="Dev",
                model={"tier": "medium", "priority": "fastest"},
            )

    def test_dict_model_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateAgentConfig(
                role="Dev",
                model={"tier": "medium", "unknown_field": "x"},
            )

    def test_dict_model_empty_uses_defaults(self) -> None:
        a = TemplateAgentConfig(role="Dev", model={})
        assert a.model == {}

    def test_string_model_invalid_tier_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateAgentConfig(role="Dev", model="xlarge")

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

    def test_remove_alias(self) -> None:
        """_remove alias maps to remove field."""
        d = TemplateDepartmentConfig.model_validate(
            {"name": "executive", "_remove": True},
        )
        assert d.remove is True

    def test_remove_defaults_false(self) -> None:
        """``remove`` defaults to ``False`` when not supplied."""
        d = TemplateDepartmentConfig(name="engineering")
        assert d.remove is False

    def test_head_merge_id_default_none(self) -> None:
        d = TemplateDepartmentConfig(name="eng")
        assert d.head_merge_id is None

    def test_head_merge_id_accepted_with_head_role(self) -> None:
        d = TemplateDepartmentConfig(
            name="eng",
            head_role="CTO",
            head_merge_id="cto-1",
        )
        assert d.head_merge_id == "cto-1"

    def test_head_merge_id_blank_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateDepartmentConfig(
                name="eng",
                head_role="CTO",
                head_merge_id="",
            )

    def test_head_merge_id_whitespace_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TemplateDepartmentConfig(
                name="eng",
                head_role="CTO",
                head_merge_id="   ",
            )

    def test_head_merge_id_without_head_role_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="head_merge_id is set but head_role is missing",
        ):
            TemplateDepartmentConfig(
                name="eng",
                head_merge_id="cto-1",
            )

    def test_ceremony_policy_none_by_default(self) -> None:
        d = TemplateDepartmentConfig(name="eng")
        assert d.ceremony_policy is None

    def test_ceremony_policy_accepted(self) -> None:
        d = TemplateDepartmentConfig(
            name="eng",
            ceremony_policy={"strategy": "calendar"},
        )
        assert d.ceremony_policy == {"strategy": "calendar"}

    def test_ceremony_policy_complex_dict_accepted(self) -> None:
        d = TemplateDepartmentConfig(
            name="marketing",
            ceremony_policy={
                "strategy": "event_driven",
                "auto_transition": True,
                "transition_threshold": 0.8,
            },
        )
        assert d.ceremony_policy is not None
        assert d.ceremony_policy["transition_threshold"] == 0.8


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
        assert m.skill_patterns == ()

    def test_valid_full(self) -> None:
        m = TemplateMetadata(
            name="My Template",
            description="A description",
            version="2.0.0",
            company_type=CompanyType.STARTUP,
            min_agents=2,
            max_agents=10,
            tags=("startup", "mvp"),
            skill_patterns=(
                SkillPattern.TOOL_WRAPPER,
                SkillPattern.PIPELINE,
            ),
        )
        assert m.version == "2.0.0"
        assert m.tags == ("startup", "mvp")
        assert m.skill_patterns == (
            SkillPattern.TOOL_WRAPPER,
            SkillPattern.PIPELINE,
        )

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

    def test_duplicate_skill_patterns_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate skill_patterns"):
            TemplateMetadata(
                name="Dupes",
                company_type=CompanyType.CUSTOM,
                skill_patterns=(
                    SkillPattern.PIPELINE,
                    SkillPattern.PIPELINE,
                ),
            )


# ── CompanyTemplate ──────────────────────────────────────────────


@pytest.mark.unit
class TestCompanyTemplate:
    def test_valid_minimal(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        t = CompanyTemplate.model_validate(make_template_dict())
        assert t.metadata.name == "Test"
        assert len(t.agents) == 1
        assert t.workflow == "agile_kanban"
        assert t.communication == "hybrid"
        assert t.budget_monthly == 50.0
        assert t.autonomy == {"level": "semi"}

    def test_agent_count_below_min_rejected(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        with pytest.raises(ValidationError, match="minimum"):
            CompanyTemplate.model_validate(
                make_template_dict(
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
        make_template_dict: TemplateDictFactory,
    ) -> None:
        agents = tuple({"role": f"Dev{i}", "level": "mid"} for i in range(5))
        with pytest.raises(ValidationError, match="maximum"):
            CompanyTemplate.model_validate(
                make_template_dict(
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
        make_template_dict: TemplateDictFactory,
    ) -> None:
        with pytest.raises(ValidationError, match="Duplicate variable names"):
            CompanyTemplate.model_validate(
                make_template_dict(
                    variables=(
                        {"name": "x", "var_type": "str"},
                        {"name": "x", "var_type": "int"},
                    ),
                )
            )

    def test_duplicate_department_names_rejected(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        with pytest.raises(ValidationError, match="Duplicate department names"):
            CompanyTemplate.model_validate(
                make_template_dict(
                    departments=(
                        {"name": "eng", "budget_percent": 50},
                        {"name": "eng", "budget_percent": 50},
                    ),
                )
            )

    def test_unique_variables_accepted(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        t = CompanyTemplate.model_validate(
            make_template_dict(
                variables=(
                    {"name": "x"},
                    {"name": "y"},
                ),
            )
        )
        assert len(t.variables) == 2

    def test_autonomy_float_rejected(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        """Bare float for autonomy is no longer accepted."""
        with pytest.raises(ValidationError):
            CompanyTemplate.model_validate(make_template_dict(autonomy=0.5))

    def test_negative_budget_rejected(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        with pytest.raises(ValidationError):
            CompanyTemplate.model_validate(make_template_dict(budget_monthly=-10.0))

    def test_blank_workflow_rejected(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        with pytest.raises(ValidationError):
            CompanyTemplate.model_validate(make_template_dict(workflow=""))

    def test_whitespace_workflow_rejected(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        with pytest.raises(ValidationError):
            CompanyTemplate.model_validate(make_template_dict(workflow="   "))

    def test_blank_communication_rejected(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        with pytest.raises(ValidationError):
            CompanyTemplate.model_validate(make_template_dict(communication=""))

    def test_whitespace_communication_rejected(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        with pytest.raises(ValidationError):
            CompanyTemplate.model_validate(make_template_dict(communication="   "))

    def test_workflow_handoffs_default_empty(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        t = CompanyTemplate.model_validate(make_template_dict())
        assert t.workflow_handoffs == ()

    def test_escalation_paths_default_empty(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        t = CompanyTemplate.model_validate(make_template_dict())
        assert t.escalation_paths == ()

    def test_workflow_handoffs_accepted(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        t = CompanyTemplate.model_validate(
            make_template_dict(
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
        make_template_dict: TemplateDictFactory,
    ) -> None:
        t = CompanyTemplate.model_validate(
            make_template_dict(
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
        make_template_dict: TemplateDictFactory,
    ) -> None:
        t = CompanyTemplate.model_validate(
            make_template_dict(extends="startup", agents=()),
        )
        assert t.extends == "startup"

    def test_extends_normalizes_case(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        t = CompanyTemplate.model_validate(
            make_template_dict(extends="  StartUp  ", agents=()),
        )
        assert t.extends == "startup"

    def test_extends_skips_agent_count_validation(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        t = CompanyTemplate.model_validate(
            make_template_dict(extends="startup", agents=()),
        )
        assert len(t.agents) == 0

    def test_frozen(
        self,
        make_template_dict: TemplateDictFactory,
    ) -> None:
        t = CompanyTemplate.model_validate(make_template_dict())
        with pytest.raises(ValidationError):
            t.workflow = "scrum"  # type: ignore[assignment,misc]
