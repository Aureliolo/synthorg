"""Tests for company structure and configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.company import (
    Company,
    CompanyConfig,
    Department,
    DepartmentPolicies,
    EscalationPath,
    HRRegistry,
    ReviewRequirements,
    Team,
    WorkflowHandoff,
    _identity_key,
)
from synthorg.core.enums import CompanyType
from synthorg.security.timeout.config import (
    DenyOnTimeoutConfig,
    WaitForeverConfig,
)

from .conftest import (
    CompanyConfigFactory,
    CompanyFactory,
    DepartmentFactory,
    HRRegistryFactory,
    TeamFactory,
)

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

    def test_duplicate_members_case_insensitive(self) -> None:
        """Reject members that differ only by case."""
        with pytest.raises(ValidationError, match="Duplicate members"):
            Team(
                name="backend",
                lead="lead",
                members=("Alice", "alice"),
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

    def test_head_defaults_to_none(self) -> None:
        """Department can be created without a head (defaults to None)."""
        dept = Department(name="Test")
        assert dept.head is None
        assert dept.budget_percent == 0.0

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

    def test_duplicate_team_names_case_insensitive(self) -> None:
        """Reject team names that differ only by case."""
        with pytest.raises(ValidationError, match="Duplicate team names"):
            Department(
                name="Eng",
                head="head",
                teams=(
                    Team(name="Backend", lead="a"),
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

    def test_with_policies(self) -> None:
        """Accept department with custom policies."""
        dept = Department(
            name="eng",
            head="cto",
            policies=DepartmentPolicies(
                review_requirements=ReviewRequirements(min_reviewers=2),
            ),
        )
        assert dept.policies.review_requirements.min_reviewers == 2

    def test_backward_compatible_policies_defaults(self) -> None:
        """Default policies for backward compatibility."""
        dept = Department(name="eng", head="cto")
        assert isinstance(dept.policies, DepartmentPolicies)

    def test_ceremony_policy_none_by_default(self) -> None:
        """ceremony_policy defaults to None (inherits project-level)."""
        dept = Department(name="eng", head="cto")
        assert dept.ceremony_policy is None

    def test_ceremony_policy_accepted(self) -> None:
        """Accept a ceremony policy dict override."""
        dept = Department(
            name="marketing",
            head="head",
            ceremony_policy={"strategy": "calendar"},
        )
        assert dept.ceremony_policy is not None
        assert dept.ceremony_policy["strategy"] == "calendar"

    def test_ceremony_policy_complex_dict(self) -> None:
        """Accept ceremony_policy with multiple fields."""
        dept = Department(
            name="design",
            head="head",
            ceremony_policy={
                "strategy": "event_driven",
                "transition_threshold": 0.8,
            },
        )
        assert dept.ceremony_policy is not None
        assert dept.ceremony_policy["transition_threshold"] == 0.8

    def test_ceremony_policy_is_defensively_copied(self) -> None:
        """Mutating the source dict must not affect the stored policy."""
        source = {"strategy": "calendar", "cadence": "weekly"}
        dept = Department(
            name="marketing",
            head="head",
            ceremony_policy=source,
        )

        # Mutate the original dict
        source["strategy"] = "task_driven"
        source["new_key"] = "surprise"

        # Department's copy must be unaffected
        assert dept.ceremony_policy is not None
        assert dept.ceremony_policy["strategy"] == "calendar"
        assert "new_key" not in dept.ceremony_policy


# ── CompanyConfig ──────────────────────────────────────────────────


@pytest.mark.unit
class TestCompanyConfig:
    """Tests for CompanyConfig defaults, autonomy bounds, and validation."""

    def test_defaults(self) -> None:
        """Verify default autonomy config, budget, and communication pattern.

        Autonomy default flipped SEMI -> SUPERVISED (2026-04-23, #1538):
        fresh installs now queue approvals for state-mutating actions.
        """
        cfg = CompanyConfig()
        assert cfg.autonomy.level == AutonomyLevel.SUPERVISED
        assert cfg.budget_monthly == 100.0
        assert cfg.communication_pattern == "hybrid"
        assert cfg.tool_access_default == ()

    def test_autonomy_float_rejected(self) -> None:
        """Bare float for autonomy is no longer accepted."""
        with pytest.raises(ValidationError):
            CompanyConfig(autonomy=0.5)  # type: ignore[arg-type]

    def test_autonomy_config_direct(self) -> None:
        """Accept AutonomyConfig dict directly."""
        cfg = CompanyConfig(autonomy={"level": "full"})  # type: ignore[arg-type]
        assert cfg.autonomy.level == AutonomyLevel.FULL

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
            cfg.budget_monthly = 999.0  # type: ignore[misc]

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

    def test_duplicate_active_agents_case_insensitive(self) -> None:
        """Reject active agents that differ only by case."""
        with pytest.raises(ValidationError, match="Duplicate entries"):
            HRRegistry(active_agents=("Alice", "alice"))

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

    def test_headless_department_in_company(self) -> None:
        """Company accepts departments with head=None."""
        dept = Department(name="Eng")
        co = Company(name="Test", departments=(dept,))
        assert co.departments[0].head is None

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

    def test_with_workflow_handoffs(self) -> None:
        """Accept a company with workflow handoffs referencing declared departments."""
        depts = (
            Department(name="engineering", head="cto"),
            Department(name="qa", head="qa_lead"),
        )
        handoff = WorkflowHandoff(
            from_department="engineering",
            to_department="qa",
            trigger="code_complete",
        )
        co = Company(
            name="Test",
            departments=depts,
            workflow_handoffs=(handoff,),
        )
        assert len(co.workflow_handoffs) == 1

    def test_with_escalation_paths(self) -> None:
        """Accept a company with escalation paths referencing declared departments."""
        depts = (
            Department(name="engineering", head="cto"),
            Department(name="executive", head="ceo"),
        )
        esc = EscalationPath(
            from_department="engineering",
            to_department="executive",
            condition="critical_failure",
            priority_boost=2,
        )
        co = Company(
            name="Test",
            departments=depts,
            escalation_paths=(esc,),
        )
        assert len(co.escalation_paths) == 1

    def test_workflow_handoff_unknown_department_rejected(self) -> None:
        """Reject workflow handoffs referencing unknown departments."""
        depts = (Department(name="engineering", head="cto"),)
        handoff = WorkflowHandoff(
            from_department="engineering",
            to_department="qa",
            trigger="code_complete",
        )
        with pytest.raises(ValidationError, match="unknown department"):
            Company(
                name="Test",
                departments=depts,
                workflow_handoffs=(handoff,),
            )

    def test_escalation_path_unknown_department_rejected(self) -> None:
        """Reject escalation paths referencing unknown departments."""
        depts = (Department(name="engineering", head="cto"),)
        esc = EscalationPath(
            from_department="engineering",
            to_department="executive",
            condition="critical_failure",
        )
        with pytest.raises(ValidationError, match="unknown department"):
            Company(
                name="Test",
                departments=depts,
                escalation_paths=(esc,),
            )


# ── CompanyConfig approval timeout ────────────────────────────────


@pytest.mark.unit
class TestCompanyConfigApprovalTimeout:
    """Tests for CompanyConfig.approval_timeout field."""

    def test_default_approval_timeout(self) -> None:
        """CompanyConfig() defaults to WaitForeverConfig."""
        cfg = CompanyConfig()
        assert isinstance(cfg.approval_timeout, WaitForeverConfig)
        assert cfg.approval_timeout.policy == "wait"

    def test_custom_approval_timeout(self) -> None:
        """Can pass a DenyOnTimeoutConfig as approval_timeout."""
        deny_cfg = DenyOnTimeoutConfig(timeout_minutes=60.0)
        cfg = CompanyConfig(approval_timeout=deny_cfg)
        assert isinstance(cfg.approval_timeout, DenyOnTimeoutConfig)
        assert cfg.approval_timeout.timeout_minutes == 60.0

    def test_approval_timeout_from_dict(self) -> None:
        """Can construct from dict with discriminated union."""
        cfg = CompanyConfig.model_validate(
            {"approval_timeout": {"policy": "deny", "timeout_minutes": 60}}
        )
        assert isinstance(cfg.approval_timeout, DenyOnTimeoutConfig)
        assert cfg.approval_timeout.timeout_minutes == 60.0


# ── _identity_key ──────────────────────────────────────────────────


@pytest.mark.unit
class TestIdentityKey:
    """Tests for the namespaced identity-key helper."""

    def test_id_branch_uses_id_namespace(self) -> None:
        """When ``id_`` is provided it takes precedence and is normalized."""
        assert _identity_key("Backend Developer", "backend-1") == ("id", "backend-1")

    def test_name_branch_uses_name_namespace(self) -> None:
        """When ``id_`` is None, the name is used in the ``name`` namespace."""
        assert _identity_key("Backend Developer", None) == (
            "name",
            "backend developer",
        )

    def test_id_branch_normalizes_case_and_whitespace(self) -> None:
        """ID values are case-folded and stripped via ``normalize_identifier``."""
        assert _identity_key("Anything", "  Backend-1  ") == ("id", "backend-1")

    def test_name_branch_normalizes_case_and_whitespace(self) -> None:
        """Name values are case-folded and stripped via ``normalize_identifier``."""
        assert _identity_key("  Backend Developer  ", None) == (
            "name",
            "backend developer",
        )

    def test_namespaces_prevent_cross_collision(self) -> None:
        """Same canonical string in different namespaces does not collide."""
        id_keyed = _identity_key("ignored", "alice")
        name_keyed = _identity_key("alice", None)
        assert id_keyed != name_keyed
        assert id_keyed[1] == name_keyed[1] == "alice"
