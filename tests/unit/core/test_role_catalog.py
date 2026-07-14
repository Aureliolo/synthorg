"""Tests for the built-in role catalog and reporting graph."""

import pytest
from pydantic import ValidationError

from synthorg.core.authority import reporting_chain, role_depth
from synthorg.core.role import Role
from synthorg.core.role_catalog import (
    BUILTIN_ROLES,
    get_builtin_role,
)
from synthorg.organization.enums import DepartmentName

# ── Reporting graph ────────────────────────────────────────────────


@pytest.mark.unit
class TestReportingGraph:
    """The reporting graph is rooted at the CEO and always terminates."""

    def test_ceo_is_root(self) -> None:
        """The CEO reports to no one (reporting depth 0)."""
        assert role_depth("CEO") == 0

    def test_every_role_reaches_the_ceo(self) -> None:
        """Every built-in role's reporting chain ends at the CEO."""
        for role in BUILTIN_ROLES:
            if role.name == "CEO":
                continue
            chain = reporting_chain(role.name)
            assert chain, f"{role.name} has an empty reporting chain"
            assert chain[-1] == "ceo", f"{role.name} does not report up to the CEO"

    def test_c_suite_reports_to_ceo(self) -> None:
        """Each non-CEO C-suite role reports directly to the CEO."""
        for name in ("CTO", "CFO", "COO", "CPO"):
            role = get_builtin_role(name)
            assert role is not None
            assert role.reports_to == "CEO"


# ── Builtin Roles ─────────────────────────────────────────────────


@pytest.mark.unit
class TestBuiltinRoles:
    """Tests for the BUILTIN_ROLES tuple completeness and invariants."""

    def test_has_33_roles(self) -> None:
        """Verify BUILTIN_ROLES contains exactly 33 roles (32 + Red Team)."""
        assert len(BUILTIN_ROLES) == 33

    def test_red_team_role_present(self) -> None:
        """The built-in Red Team role is registered under its catalogued name."""
        names = {r.name for r in BUILTIN_ROLES}
        assert "Red Team" in names

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
        """Verify all expected C-suite roles exist in the executive department."""
        c_suite = [r for r in BUILTIN_ROLES if r.department is DepartmentName.EXECUTIVE]
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
            "Knowledge Architect",
            "Red Team",
        ],
    )
    def test_all_roles_lookupable(self, name: str) -> None:
        """Verify each built-in role is findable by its exact name."""
        role = get_builtin_role(name)
        assert role is not None, f"Role {name!r} not found in catalog"
        assert role.name == name


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
