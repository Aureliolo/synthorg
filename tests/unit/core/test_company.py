"""Tests for company structure and configuration models."""

import pytest
from pydantic import ValidationError

from ai_company.core.company import (
    Company,
    CompanyConfig,
    Department,
    HRRegistry,
    Team,
)
from ai_company.core.enums import CompanyType

from .conftest import (
    CompanyConfigFactory,
    CompanyFactory,
    DepartmentFactory,
    HRRegistryFactory,
    TeamFactory,
)

pytestmark = pytest.mark.timeout(30)

# ── Team ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestTeam:
    """Tests for Team validation, defaults, and immutability."""

    def test_valid_team(self) -> None:
        """Verify a valid team persists all provided fields."""
        team = Team(
            name="backend",
            lead="backend_lead",
            members=("dev_1", "dev_2"),
        )
        assert team.name == "backend"
        assert team.lead == "backend_lead"
        assert len(team.members) == 2

    def test_defaults(self) -> None:
        """Verify default empty members tuple."""
        team = Team(name="test", lead="lead")
        assert team.members == ()

    def test_empty_name_rejected(self) -> None:
        """Reject empty team name."""
        with pytest.raises(ValidationError):
            Team(name="", lead="lead")

    def test_empty_lead_rejected(self) -> None:
        """Reject empty lead name."""
        with pytest.raises(ValidationError):
            Team(name="test", lead="")

    def test_whitespace_name_rejected(self) -> None:
        """Reject whitespace-only team name."""
        with pytest.raises(ValidationError):
            Team(name="   ", lead="lead")

    def test_whitespace_lead_rejected(self) -> None:
        """Reject whitespace-only lead name."""
        with pytest.raises(ValidationError):
            Team(name="test", lead="   ")

    def test_empty_member_name_rejected(self) -> None:
        """Reject empty string in members tuple."""
        with pytest.raises(ValidationError, match="at least 1 character"):
            Team(name="test", lead="lead", members=("dev", ""))

    def test_whitespace_member_name_rejected(self) -> None:
        """Reject whitespace-only member name."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            Team(name="test", lead="lead", members=("  ",))

    def test_duplicate_members_rejected(self) -> None:
        """Reject duplicate member names in a team."""
        with pytest.raises(ValidationError, match="Duplicate members"):
            Team(
                name="backend",
                lead="lead",
                members=("alice", "bob", "alice"),
            )

    def test_frozen(self) -> None:
        """Ensure Team is immutable."""
        team = Team(name="test", lead="lead")
        with pytest.raises(ValidationError):
            team.name = "changed"  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid Team."""
        team = TeamFactory.build()
        assert isinstance(team, Team)


# ── Department ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestDepartment:
    """Tests for Department validation, budget constraints, and immutability."""

    def test_valid_department(self, sample_department: Department) -> None:
        """Verify fixture-provided department has expected fields."""
        assert sample_department.name == "Engineering"
        assert sample_department.head == "cto"
        assert sample_department.budget_percent == 60.0
        assert len(sample_department.teams) == 1

    def test_defaults(self) -> None:
        """Verify default budget_percent and empty teams."""
        dept = Department(name="Test", head="head")
        assert dept.budget_percent == 0.0
        assert dept.teams == ()

    def test_budget_percent_zero(self) -> None:
        """Accept budget_percent at lower boundary (0.0)."""
        dept = Department(name="Test", head="head", budget_percent=0.0)
        assert dept.budget_percent == 0.0

    def test_budget_percent_hundred(self) -> None:
        """Accept budget_percent at upper boundary (100.0)."""
        dept = Department(name="Test", head="head", budget_percent=100.0)
        assert dept.budget_percent == 100.0

    def test_budget_percent_negative_rejected(self) -> None:
        """Reject negative budget_percent."""
        with pytest.raises(ValidationError):
            Department(name="Test", head="head", budget_percent=-1.0)

    def test_budget_percent_over_hundred_rejected(self) -> None:
        """Reject budget_percent above 100."""
        with pytest.raises(ValidationError):
            Department(name="Test", head="head", budget_percent=100.1)

    def test_multiple_distinct_teams_accepted(self) -> None:
        """Accept department with multiple uniquely named teams."""
        dept = Department(
            name="Engineering",
            head="cto",
            teams=(
                Team(name="backend", lead="a"),
                Team(name="frontend", lead="b"),
                Team(name="infra", lead="c"),
            ),
        )
        assert len(dept.teams) == 3

    def test_whitespace_name_rejected(self) -> None:
        """Reject whitespace-only department name."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            Department(name="   ", head="head")

    def test_whitespace_head_rejected(self) -> None:
        """Reject whitespace-only head name."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            Department(name="Eng", head="   ")

    def test_duplicate_team_names_rejected(self) -> None:
        """Reject duplicate team names within a department."""
        with pytest.raises(ValidationError, match="Duplicate team names"):
            Department(
                name="Eng",
                head="head",
                teams=(
                    Team(name="backend", lead="a"),
                    Team(name="backend", lead="b"),
                ),
            )

    def test_frozen(self, sample_department: Department) -> None:
        """Ensure Department is immutable."""
        with pytest.raises(ValidationError):
            sample_department.name = "Changed"  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid Department with sane budget."""
        dept = DepartmentFactory.build()
        assert isinstance(dept, Department)
        assert 0.0 <= dept.budget_percent <= 100.0


# ── CompanyConfig ──────────────────────────────────────────────────


@pytest.mark.unit
class TestCompanyConfig:
    """Tests for CompanyConfig defaults, autonomy bounds, and validation."""

    def test_defaults(self) -> None:
        """Verify default autonomy, budget, and communication pattern."""
        cfg = CompanyConfig()
        assert cfg.autonomy == 0.5
        assert cfg.budget_monthly == 100.0
        assert cfg.communication_pattern == "hybrid"
        assert cfg.tool_access_default == ()

    def test_autonomy_boundaries(self) -> None:
        """Accept autonomy at both boundaries (0.0 and 1.0)."""
        low = CompanyConfig(autonomy=0.0)
        high = CompanyConfig(autonomy=1.0)
        assert low.autonomy == 0.0
        assert high.autonomy == 1.0

    def test_autonomy_below_zero_rejected(self) -> None:
        """Reject autonomy below 0.0."""
        with pytest.raises(ValidationError):
            CompanyConfig(autonomy=-0.1)

    def test_autonomy_above_one_rejected(self) -> None:
        """Reject autonomy above 1.0."""
        with pytest.raises(ValidationError):
            CompanyConfig(autonomy=1.1)

    def test_budget_negative_rejected(self) -> None:
        """Reject negative monthly budget."""
        with pytest.raises(ValidationError):
            CompanyConfig(budget_monthly=-1.0)

    def test_empty_communication_pattern_rejected(self) -> None:
        """Reject empty communication_pattern string."""
        with pytest.raises(ValidationError):
            CompanyConfig(communication_pattern="")

    def test_whitespace_communication_pattern_rejected(self) -> None:
        """Reject whitespace-only communication_pattern."""
        with pytest.raises(ValidationError):
            CompanyConfig(communication_pattern="   ")

    def test_empty_tool_access_entry_rejected(self) -> None:
        """Reject empty string in tool_access_default tuple."""
        with pytest.raises(ValidationError, match="at least 1 character"):
            CompanyConfig(tool_access_default=("git", ""))

    def test_frozen(self) -> None:
        """Ensure CompanyConfig is immutable."""
        cfg = CompanyConfig()
        with pytest.raises(ValidationError):
            cfg.autonomy = 1.0  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid CompanyConfig."""
        cfg = CompanyConfigFactory.build()
        assert isinstance(cfg, CompanyConfig)


# ── HRRegistry ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestHRRegistry:
    """Tests for HRRegistry defaults, uniqueness constraints, and validation."""

    def test_defaults(self) -> None:
        """Verify default empty tuples for all fields."""
        hr = HRRegistry()
        assert hr.active_agents == ()
        assert hr.available_roles == ()
        assert hr.hiring_queue == ()

    def test_custom_values(self) -> None:
        """Verify explicitly provided values are persisted."""
        hr = HRRegistry(
            active_agents=("agent_1",),
            available_roles=("dev", "pm"),
            hiring_queue=("designer",),
        )
        assert len(hr.active_agents) == 1
        assert len(hr.available_roles) == 2

    def test_duplicate_active_agents_rejected(self) -> None:
        """Reject duplicate entries in active_agents."""
        with pytest.raises(ValidationError, match="Duplicate entries"):
            HRRegistry(active_agents=("alice", "alice"))

    def test_empty_active_agent_rejected(self) -> None:
        """Reject empty string in active_agents."""
        with pytest.raises(ValidationError, match="at least 1 character"):
            HRRegistry(active_agents=("",))

    def test_empty_available_role_rejected(self) -> None:
        """Reject whitespace-only entry in available_roles."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            HRRegistry(available_roles=("  ",))

    def test_empty_hiring_queue_entry_rejected(self) -> None:
        """Reject empty string in hiring_queue."""
        with pytest.raises(ValidationError, match="at least 1 character"):
            HRRegistry(hiring_queue=("",))

    def test_duplicate_available_roles_accepted(self) -> None:
        """Duplicates are intentionally allowed in available_roles."""
        hr = HRRegistry(available_roles=("dev", "dev"))
        assert hr.available_roles == ("dev", "dev")

    def test_duplicate_hiring_queue_accepted(self) -> None:
        """Duplicates are intentionally allowed in hiring_queue."""
        hr = HRRegistry(hiring_queue=("pm", "pm"))
        assert hr.hiring_queue == ("pm", "pm")

    def test_frozen(self) -> None:
        """Ensure HRRegistry is immutable."""
        hr = HRRegistry()
        with pytest.raises(ValidationError):
            hr.active_agents = ("new",)  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid HRRegistry."""
        hr = HRRegistryFactory.build()
        assert isinstance(hr, HRRegistry)


# ── Company ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestCompany:
    """Tests for Company construction, budget validation, and serialization."""

    def test_valid_company(self, sample_company: Company) -> None:
        """Verify fixture-provided company has expected fields."""
        assert sample_company.name == "Test Corp"
        assert len(sample_company.departments) == 1
        assert sample_company.config.budget_monthly == 100.0

    def test_defaults(self) -> None:
        """Verify default type, departments, config, and hr_registry."""
        co = Company(name="Minimal")
        assert co.type is CompanyType.CUSTOM
        assert co.departments == ()
        assert isinstance(co.config, CompanyConfig)
        assert isinstance(co.hr_registry, HRRegistry)

    def test_budget_sum_at_100_accepted(self) -> None:
        """Accept departments whose budget_percent sums to exactly 100."""
        depts = (
            Department(name="A", head="a", budget_percent=60.0),
            Department(name="B", head="b", budget_percent=40.0),
        )
        co = Company(name="Full Budget", departments=depts)
        assert sum(d.budget_percent for d in co.departments) == 100.0

    def test_budget_sum_under_100_accepted(self) -> None:
        """Accept departments whose budget_percent sums to less than 100."""
        depts = (
            Department(name="A", head="a", budget_percent=50.0),
            Department(name="B", head="b", budget_percent=30.0),
        )
        co = Company(name="With Reserve", departments=depts)
        assert sum(d.budget_percent for d in co.departments) == 80.0

    def test_budget_sum_over_100_rejected(self) -> None:
        """Reject departments whose budget_percent exceeds 100."""
        depts = (
            Department(name="A", head="a", budget_percent=60.0),
            Department(name="B", head="b", budget_percent=50.0),
        )
        with pytest.raises(ValidationError, match="exceeding 100%"):
            Company(name="Over Budget", departments=depts)

    def test_budget_sum_barely_over_100_rejected(self) -> None:
        """Reject budget_percent sums just barely over 100."""
        depts = (
            Department(name="A", head="a", budget_percent=50.01),
            Department(name="B", head="b", budget_percent=50.0),
        )
        with pytest.raises(ValidationError, match="exceeding 100%"):
            Company(name="Just Over", departments=depts)

    def test_budget_sum_float_precision_accepted(self) -> None:
        """Classic float artifacts (e.g. 33.33+33.33+33.34) should not cause
        false rejections thanks to rounding."""
        depts = (
            Department(name="A", head="a", budget_percent=33.33),
            Department(name="B", head="b", budget_percent=33.33),
            Department(name="C", head="c", budget_percent=33.34),
        )
        co = Company(name="Float Precision", departments=depts)
        assert len(co.departments) == 3

    def test_duplicate_department_names_rejected(self) -> None:
        """Reject duplicate department names within a company."""
        depts = (
            Department(name="Engineering", head="a", budget_percent=30.0),
            Department(name="Engineering", head="b", budget_percent=20.0),
        )
        with pytest.raises(ValidationError, match="Duplicate department names"):
            Company(name="Dup Depts", departments=depts)

    def test_empty_name_rejected(self) -> None:
        """Reject empty company name."""
        with pytest.raises(ValidationError):
            Company(name="")

    def test_whitespace_name_rejected(self) -> None:
        """Reject whitespace-only company name."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            Company(name="   ")

    def test_empty_departments_accepted(self) -> None:
        """Accept a company with no departments."""
        co = Company(name="Empty")
        assert co.departments == ()

    def test_frozen(self, sample_company: Company) -> None:
        """Ensure Company is immutable."""
        with pytest.raises(ValidationError):
            sample_company.name = "Changed"  # type: ignore[misc]

    def test_model_copy_update(self, sample_company: Company) -> None:
        """Verify model_copy creates a new instance without mutating the original."""
        updated = sample_company.model_copy(update={"name": "New Corp"})
        assert updated.name == "New Corp"
        assert sample_company.name == "Test Corp"

    def test_json_roundtrip(self, sample_company: Company) -> None:
        """Verify JSON serialization and deserialization preserves fields."""
        json_str = sample_company.model_dump_json()
        restored = Company.model_validate_json(json_str)
        assert restored.name == sample_company.name
        assert len(restored.departments) == len(sample_company.departments)

    def test_factory(self) -> None:
        """Verify factory produces a valid Company."""
        co = CompanyFactory.build()
        assert isinstance(co, Company)
