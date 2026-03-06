"""Tests for role and skill domain models."""

import pytest
from pydantic import ValidationError

from ai_company.core.enums import (
    DepartmentName,
    ProficiencyLevel,
    SeniorityLevel,
    SkillCategory,
)
from ai_company.core.role import Authority, CustomRole, Role, SeniorityInfo, Skill

from .conftest import (
    AuthorityFactory,
    CustomRoleFactory,
    RoleFactory,
    SeniorityInfoFactory,
    SkillFactory,
)

pytestmark = pytest.mark.timeout(30)

# ── Skill ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSkill:
    def test_valid_skill(self, sample_skill: Skill) -> None:
        assert sample_skill.name == "python"
        assert sample_skill.category is SkillCategory.ENGINEERING
        assert sample_skill.proficiency is ProficiencyLevel.ADVANCED

    def test_default_proficiency(self) -> None:
        skill = Skill(name="testing", category=SkillCategory.QA)
        assert skill.proficiency is ProficiencyLevel.INTERMEDIATE

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Skill(name="", category=SkillCategory.ENGINEERING)

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Skill(name="   ", category=SkillCategory.ENGINEERING)

    def test_frozen(self, sample_skill: Skill) -> None:
        with pytest.raises(ValidationError):
            sample_skill.name = "rust"  # type: ignore[misc]

    def test_json_roundtrip(self, sample_skill: Skill) -> None:
        json_str = sample_skill.model_dump_json()
        restored = Skill.model_validate_json(json_str)
        assert restored == sample_skill

    def test_factory_creates_valid_skill(self) -> None:
        skill = SkillFactory.build()
        assert isinstance(skill, Skill)
        assert len(skill.name) >= 1


# ── Authority ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestAuthority:
    def test_valid_authority(self, sample_authority: Authority) -> None:
        assert sample_authority.can_approve == ("code_reviews",)
        assert sample_authority.reports_to == "engineering_lead"
        assert sample_authority.budget_limit == 5.0

    def test_defaults(self) -> None:
        auth = Authority()
        assert auth.can_approve == ()
        assert auth.reports_to is None
        assert auth.can_delegate_to == ()
        assert auth.budget_limit == 0.0

    def test_negative_budget_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Authority(budget_limit=-1.0)

    def test_empty_reports_to_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Authority(reports_to="")

    def test_empty_can_approve_entry_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            Authority(can_approve=("code_review", ""))

    def test_whitespace_can_delegate_to_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Authority(can_delegate_to=("  ",))

    def test_whitespace_reports_to_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Authority(reports_to="   ")

    def test_frozen(self, sample_authority: Authority) -> None:
        with pytest.raises(ValidationError):
            sample_authority.budget_limit = 10.0  # type: ignore[misc]

    def test_model_copy_update(self, sample_authority: Authority) -> None:
        updated = sample_authority.model_copy(update={"budget_limit": 10.0})
        assert updated.budget_limit == 10.0
        assert sample_authority.budget_limit == 5.0

    def test_json_roundtrip(self, sample_authority: Authority) -> None:
        json_str = sample_authority.model_dump_json()
        restored = Authority.model_validate_json(json_str)
        assert restored == sample_authority

    def test_factory_creates_valid_authority(self) -> None:
        auth = AuthorityFactory.build()
        assert isinstance(auth, Authority)
        assert auth.budget_limit >= 0.0


# ── SeniorityInfo ─────────────────────────────────────────────────


@pytest.mark.unit
class TestSeniorityInfo:
    def test_valid_seniority_info(self) -> None:
        info = SeniorityInfo(
            level=SeniorityLevel.SENIOR,
            authority_scope="Execute, design, and review",
            typical_model_tier="medium",
            cost_tier="high",
        )
        assert info.level is SeniorityLevel.SENIOR
        assert info.cost_tier == "high"

    def test_empty_authority_scope_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SeniorityInfo(
                level=SeniorityLevel.JUNIOR,
                authority_scope="",
                typical_model_tier="small",
                cost_tier="low",
            )

    def test_empty_model_tier_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SeniorityInfo(
                level=SeniorityLevel.JUNIOR,
                authority_scope="tasks",
                typical_model_tier="",
                cost_tier="low",
            )

    def test_empty_cost_tier_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SeniorityInfo(
                level=SeniorityLevel.JUNIOR,
                authority_scope="tasks",
                typical_model_tier="small",
                cost_tier="",
            )

    def test_whitespace_authority_scope_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            SeniorityInfo(
                level=SeniorityLevel.JUNIOR,
                authority_scope="   ",
                typical_model_tier="small",
                cost_tier="low",
            )

    def test_whitespace_model_tier_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            SeniorityInfo(
                level=SeniorityLevel.JUNIOR,
                authority_scope="tasks",
                typical_model_tier="   ",
                cost_tier="low",
            )

    def test_whitespace_cost_tier_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            SeniorityInfo(
                level=SeniorityLevel.JUNIOR,
                authority_scope="tasks",
                typical_model_tier="small",
                cost_tier="   ",
            )

    def test_frozen(self) -> None:
        info = SeniorityInfo(
            level=SeniorityLevel.MID,
            authority_scope="execute",
            typical_model_tier="medium",
            cost_tier="medium",
        )
        with pytest.raises(ValidationError):
            info.level = SeniorityLevel.SENIOR  # type: ignore[misc]

    def test_factory_creates_valid_seniority_info(self) -> None:
        info = SeniorityInfoFactory.build()
        assert isinstance(info, SeniorityInfo)


# ── Role ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRole:
    def test_valid_role(self, sample_role: Role) -> None:
        assert sample_role.name == "Backend Developer"
        assert sample_role.department is DepartmentName.ENGINEERING
        assert "python" in sample_role.required_skills

    def test_defaults(self) -> None:
        role = Role(name="Test Role", department=DepartmentName.ENGINEERING)
        assert role.required_skills == ()
        assert role.authority_level is SeniorityLevel.MID
        assert role.tool_access == ()
        assert role.system_prompt_template is None
        assert role.description == ""

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Role(name="", department=DepartmentName.ENGINEERING)

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Role(name="   ", department=DepartmentName.ENGINEERING)

    def test_whitespace_system_prompt_template_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Role(
                name="Dev",
                department=DepartmentName.ENGINEERING,
                system_prompt_template="   ",
            )

    def test_invalid_department_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Role(name="Test", department="not_a_department")  # type: ignore[arg-type]

    def test_empty_required_skill_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            Role(
                name="Dev",
                department=DepartmentName.ENGINEERING,
                required_skills=("python", ""),
            )

    def test_whitespace_tool_access_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Role(
                name="Dev",
                department=DepartmentName.ENGINEERING,
                tool_access=("  ",),
            )

    def test_frozen(self, sample_role: Role) -> None:
        with pytest.raises(ValidationError):
            sample_role.name = "Frontend Developer"  # type: ignore[misc]

    def test_json_roundtrip(self, sample_role: Role) -> None:
        json_str = sample_role.model_dump_json()
        restored = Role.model_validate_json(json_str)
        assert restored == sample_role

    def test_factory_creates_valid_role(self) -> None:
        role = RoleFactory.build()
        assert isinstance(role, Role)
        assert len(role.name) >= 1


# ── CustomRole ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestCustomRole:
    def test_with_standard_department(self) -> None:
        role = CustomRole(
            name="Blockchain Dev",
            department=DepartmentName.ENGINEERING,
            required_skills=("solidity", "web3"),
        )
        assert role.department == DepartmentName.ENGINEERING

    def test_with_custom_department_string(self) -> None:
        role = CustomRole(
            name="Blockchain Dev",
            department="blockchain",
            required_skills=("solidity", "web3"),
        )
        assert role.department == "blockchain"

    def test_defaults(self) -> None:
        role = CustomRole(name="Test", department="custom")
        assert role.required_skills == ()
        assert role.authority_level is SeniorityLevel.MID
        assert role.suggested_model is None

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CustomRole(name="", department="custom")

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            CustomRole(name="   ", department="custom")

    def test_whitespace_system_prompt_template_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            CustomRole(
                name="Dev",
                department="custom",
                system_prompt_template="   ",
            )

    def test_whitespace_suggested_model_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            CustomRole(
                name="Dev",
                department="custom",
                suggested_model="   ",
            )

    def test_empty_department_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Department name must not be empty"):
            CustomRole(name="Test", department="")

    def test_whitespace_department_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Department name must not be empty"):
            CustomRole(name="Test", department="   ")

    def test_frozen(self) -> None:
        role = CustomRole(name="Test", department="custom")
        with pytest.raises(ValidationError):
            role.name = "Changed"  # type: ignore[misc]

    def test_standard_department_as_plain_string(self) -> None:
        role = CustomRole(name="Test", department="engineering")
        assert role.department == "engineering"

    def test_whitespace_department_normalized(self) -> None:
        role = CustomRole(name="Test", department="  blockchain  ")
        assert role.department == "blockchain"

    def test_empty_required_skill_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            CustomRole(
                name="Dev",
                department="custom",
                required_skills=("solidity", ""),
            )

    def test_json_roundtrip(self) -> None:
        role = CustomRole(
            name="Custom Dev",
            department="blockchain",
            required_skills=("solidity",),
            authority_level=SeniorityLevel.SENIOR,
        )
        json_str = role.model_dump_json()
        restored = CustomRole.model_validate_json(json_str)
        assert restored == role

    def test_factory_creates_valid_custom_role(self) -> None:
        role = CustomRoleFactory.build()
        assert isinstance(role, CustomRole)
