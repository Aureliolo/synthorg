"""Tests for the built-in role catalog and seniority mappings."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from synthorg.core.enums import (
    CostTier,
    DepartmentName,
    SeniorityLevel,
)
from synthorg.core.role import Role, SeniorityInfo
from synthorg.core.role_catalog import (
    BUILTIN_ROLES,
    SENIORITY_INFO,
    get_builtin_role,
    get_seniority_info,
)

pytestmark = pytest.mark.timeout(30)

# ── Seniority Info ─────────────────────────────────────────────────


@pytest.mark.unit
class TestSeniorityInfo:
    """Tests for the SENIORITY_INFO tuple coverage and integrity."""

    def test_has_8_entries(self) -> None:
        """Verify SENIORITY_INFO contains exactly 8 mappings."""
        assert len(SENIORITY_INFO) == 8

    def test_covers_all_seniority_levels(self) -> None:
        """Verify every SeniorityLevel enum value has a mapping."""
        levels = {info.level for info in SENIORITY_INFO}
        expected = set(SeniorityLevel)
        assert levels == expected

    def test_no_duplicate_levels(self) -> None:
        """Verify no two entries share the same seniority level."""
        levels = [info.level for info in SENIORITY_INFO]
        assert len(levels) == len(set(levels))

    def test_all_entries_are_seniority_info(self) -> None:
        """Verify every entry is a SeniorityInfo instance."""
        for info in SENIORITY_INFO:
            assert isinstance(info, SeniorityInfo)

    def test_junior_is_low_cost(self) -> None:
        """Verify JUNIOR maps to LOW cost tier."""
        info = get_seniority_info(SeniorityLevel.JUNIOR)
        assert info.cost_tier == CostTier.LOW

    def test_c_suite_is_premium_cost(self) -> None:
        """Verify C_SUITE maps to PREMIUM cost tier."""
        info = get_seniority_info(SeniorityLevel.C_SUITE)
        assert info.cost_tier == CostTier.PREMIUM

    def test_senior_uses_medium_tier(self) -> None:
        """Verify SENIOR maps to 'medium' model tier."""
        info = get_seniority_info(SeniorityLevel.SENIOR)
        assert info.typical_model_tier == "medium"

    def test_all_entries_frozen(self) -> None:
        """Verify all SeniorityInfo entries are immutable."""
        for info in SENIORITY_INFO:
            with pytest.raises(ValidationError):
                info.level = SeniorityLevel.JUNIOR  # type: ignore[misc]


# ── Builtin Roles ─────────────────────────────────────────────────


@pytest.mark.unit
class TestBuiltinRoles:
    """Tests for the BUILTIN_ROLES tuple completeness and invariants."""

    def test_has_31_roles(self) -> None:
        """Verify BUILTIN_ROLES contains exactly 31 roles."""
        assert len(BUILTIN_ROLES) == 31

    def test_all_entries_are_role(self) -> None:
        """Verify every entry is a Role instance."""
        for role in BUILTIN_ROLES:
            assert isinstance(role, Role)

    def test_no_duplicate_names(self) -> None:
        """Verify no two built-in roles share the same name."""
        names = [r.name for r in BUILTIN_ROLES]
        assert len(names) == len(set(names))

    def test_all_departments_represented(self) -> None:
        """Verify every DepartmentName enum value has at least one role."""
        departments = {r.department for r in BUILTIN_ROLES}
        expected = set(DepartmentName)
        assert departments == expected

    def test_c_suite_roles_present(self) -> None:
        """Verify all expected C-suite roles exist."""
        c_suite = [
            r for r in BUILTIN_ROLES if r.authority_level is SeniorityLevel.C_SUITE
        ]
        names = {r.name for r in c_suite}
        assert {"CEO", "CTO", "CFO", "COO", "CPO"}.issubset(names)

    def test_all_roles_have_description(self) -> None:
        """Verify every built-in role has a non-empty description."""
        for role in BUILTIN_ROLES:
            assert role.description, f"{role.name} has no description"

    def test_all_roles_have_required_skills(self) -> None:
        """Verify every built-in role has at least one required skill."""
        for role in BUILTIN_ROLES:
            assert len(role.required_skills) > 0, f"{role.name} has no required_skills"

    def test_all_roles_frozen(self) -> None:
        """Verify all built-in roles are immutable."""
        for role in BUILTIN_ROLES:
            with pytest.raises(ValidationError):
                role.name = "Changed"  # type: ignore[misc]


# ── Lookup Functions ───────────────────────────────────────────────


@pytest.mark.unit
class TestGetBuiltinRole:
    """Tests for the get_builtin_role lookup function."""

    def test_exact_match(self) -> None:
        """Verify exact name lookup returns the correct role."""
        role = get_builtin_role("CEO")
        assert role is not None
        assert role.name == "CEO"

    def test_case_insensitive(self) -> None:
        """Verify lookup is case-insensitive."""
        role = get_builtin_role("ceo")
        assert role is not None
        assert role.name == "CEO"

    def test_mixed_case(self) -> None:
        """Verify lookup with mixed case and spaces works."""
        role = get_builtin_role("Backend Developer")
        assert role is not None
        assert role.name == "Backend Developer"

    def test_not_found_returns_none(self) -> None:
        """Verify unknown role name returns None."""
        assert get_builtin_role("Nonexistent Role") is None

    def test_empty_string_returns_none(self) -> None:
        """Verify empty string returns None."""
        assert get_builtin_role("") is None

    def test_whitespace_stripped(self) -> None:
        """Verify leading/trailing whitespace is stripped before lookup."""
        role = get_builtin_role("  CEO  ")
        assert role is not None
        assert role.name == "CEO"

    def test_whitespace_only_returns_none(self) -> None:
        """Verify whitespace-only input returns None."""
        assert get_builtin_role("   ") is None

    @pytest.mark.parametrize(
        "name",
        [
            "CEO",
            "CTO",
            "CFO",
            "COO",
            "CPO",
            "Product Manager",
            "UX Designer",
            "UI Designer",
            "UX Researcher",
            "Technical Writer",
            "Software Architect",
            "Frontend Developer",
            "Backend Developer",
            "Full-Stack Developer",
            "DevOps/SRE Engineer",
            "Database Engineer",
            "Security Engineer",
            "QA Lead",
            "QA Engineer",
            "Automation Engineer",
            "Performance Engineer",
            "Data Analyst",
            "Data Engineer",
            "ML Engineer",
            "Project Manager",
            "Scrum Master",
            "HR Manager",
            "Security Operations",
            "Content Writer",
            "Brand Strategist",
            "Growth Marketer",
        ],
    )
    def test_all_roles_lookupable(self, name: str) -> None:
        """Verify each built-in role is findable by its exact name."""
        role = get_builtin_role(name)
        assert role is not None, f"Role {name!r} not found in catalog"
        assert role.name == name


@pytest.mark.unit
class TestGetSeniorityInfo:
    """Tests for the get_seniority_info lookup function."""

    def test_found(self) -> None:
        """Verify lookup returns matching SeniorityInfo."""
        info = get_seniority_info(SeniorityLevel.SENIOR)
        assert info.level is SeniorityLevel.SENIOR

    @pytest.mark.parametrize("level", list(SeniorityLevel))
    def test_all_levels_lookupable(self, level: SeniorityLevel) -> None:
        """Verify every SeniorityLevel is lookupable."""
        info = get_seniority_info(level)
        assert info.level is level

    def test_raises_lookup_error_for_missing_level(self) -> None:
        """Verify LookupError when the internal map is empty."""
        with (
            patch.dict(
                "synthorg.core.role_catalog._SENIORITY_INFO_BY_LEVEL",
                {},
                clear=True,
            ),
            pytest.raises(LookupError, match="catalog may be incomplete"),
        ):
            get_seniority_info(SeniorityLevel.JUNIOR)


# ── Import-time Guard Tests ──────────────────────────────────────


@pytest.mark.unit
class TestCatalogGuards:
    """Tests for the import-time guard logic in role_catalog.py."""

    def test_no_duplicate_role_names_after_case_normalization(self) -> None:
        """Verify _BUILTIN_ROLES_BY_NAME guard: all names are unique after casefold."""
        casefolded = [r.name.casefold() for r in BUILTIN_ROLES]
        assert len(casefolded) == len(set(casefolded)), (
            "Duplicate built-in role names after case-normalization"
        )
