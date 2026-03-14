"""Tests for company structure and configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.core.company import (
    ApprovalChain,
    Company,
    CompanyConfig,
    Department,
    DepartmentPolicies,
    EscalationPath,
    HRRegistry,
    ReportingLine,
    ReviewRequirements,
    Team,
    WorkflowHandoff,
)
from synthorg.core.enums import AutonomyLevel, CompanyType
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


# ── CompanyConfig ──────────────────────────────────────────────────


@pytest.mark.unit
class TestCompanyConfig:
    """Tests for CompanyConfig defaults, autonomy bounds, and validation."""

    def test_defaults(self) -> None:
        """Verify default autonomy config, budget, and communication pattern."""
        cfg = CompanyConfig()
        assert cfg.autonomy.level == AutonomyLevel.SEMI
        assert cfg.budget_monthly == 100.0
        assert cfg.communication_pattern == "hybrid"
        assert cfg.tool_access_default == ()

    def test_autonomy_float_backward_compat(self) -> None:
        """Accept bare float and convert to AutonomyConfig via before-validator."""
        low = CompanyConfig(autonomy=0.0)  # type: ignore[arg-type]
        high = CompanyConfig(autonomy=1.0)  # type: ignore[arg-type]
        mid = CompanyConfig(autonomy=0.5)  # type: ignore[arg-type]
        supervised = CompanyConfig(autonomy=0.3)  # type: ignore[arg-type]
        assert low.autonomy.level == AutonomyLevel.LOCKED
        assert high.autonomy.level == AutonomyLevel.FULL
        assert mid.autonomy.level == AutonomyLevel.SEMI
        assert supervised.autonomy.level == AutonomyLevel.SUPERVISED

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
        co = Company(name="Test", departments=depts, workflow_handoffs=(handoff,))
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
        co = Company(name="Test", departments=depts, escalation_paths=(esc,))
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
            Company(name="Test", departments=depts, workflow_handoffs=(handoff,))

    def test_escalation_path_unknown_department_rejected(self) -> None:
        """Reject escalation paths referencing unknown departments."""
        depts = (Department(name="engineering", head="cto"),)
        esc = EscalationPath(
            from_department="engineering",
            to_department="executive",
            condition="critical_failure",
        )
        with pytest.raises(ValidationError, match="unknown department"):
            Company(name="Test", departments=depts, escalation_paths=(esc,))


# ── ReportingLine ─────────────────────────────────────────────────


@pytest.mark.unit
class TestReportingLine:
    """Tests for ReportingLine validation and immutability."""

    def test_valid(self) -> None:
        """Accept a valid reporting line."""
        r = ReportingLine(subordinate="dev", supervisor="lead")
        assert r.subordinate == "dev"
        assert r.supervisor == "lead"

    def test_self_report_rejected(self) -> None:
        """Reject self-reporting relationships."""
        with pytest.raises(ValidationError, match="cannot report to themselves"):
            ReportingLine(subordinate="dev", supervisor="dev")

    def test_self_report_case_insensitive(self) -> None:
        """Self-report check is case-insensitive."""
        with pytest.raises(ValidationError, match="cannot report to themselves"):
            ReportingLine(subordinate="Dev", supervisor="dev")

    def test_frozen(self) -> None:
        """Ensure ReportingLine is immutable."""
        r = ReportingLine(subordinate="dev", supervisor="lead")
        with pytest.raises(ValidationError):
            r.subordinate = "other"  # type: ignore[misc]


# ── ReviewRequirements ────────────────────────────────────────────


@pytest.mark.unit
class TestReviewRequirements:
    """Tests for ReviewRequirements defaults and validation."""

    def test_defaults(self) -> None:
        """Verify default values."""
        r = ReviewRequirements()
        assert r.min_reviewers == 1
        assert r.required_reviewer_roles == ()
        assert r.self_review_allowed is False

    def test_custom_values(self) -> None:
        """Accept custom configuration."""
        r = ReviewRequirements(
            min_reviewers=2,
            required_reviewer_roles=("senior",),
            self_review_allowed=True,
        )
        assert r.min_reviewers == 2
        assert r.self_review_allowed is True


# ── ApprovalChain ─────────────────────────────────────────────────


@pytest.mark.unit
class TestApprovalChain:
    """Tests for ApprovalChain validation."""

    def test_valid(self) -> None:
        """Accept a valid approval chain."""
        c = ApprovalChain(
            action_type="code_merge",
            approvers=("lead", "director"),
            min_approvals=1,
        )
        assert c.action_type == "code_merge"
        assert len(c.approvers) == 2

    def test_empty_approvers_rejected(self) -> None:
        """Reject approval chain with no approvers."""
        with pytest.raises(ValidationError, match="at least one approver"):
            ApprovalChain(action_type="deploy", approvers=())

    def test_min_approvals_exceeds_approvers_rejected(self) -> None:
        """Reject min_approvals greater than number of approvers."""
        with pytest.raises(ValidationError, match="exceeds"):
            ApprovalChain(
                action_type="deploy",
                approvers=("lead",),
                min_approvals=2,
            )

    def test_min_approvals_zero_means_all(self) -> None:
        """min_approvals=0 means all approvers required."""
        c = ApprovalChain(
            action_type="deploy",
            approvers=("lead", "director"),
            min_approvals=0,
        )
        assert c.min_approvals == 0

    def test_duplicate_approvers_rejected(self) -> None:
        """Reject approval chain with duplicate approvers."""
        with pytest.raises(ValidationError, match="Duplicate approvers"):
            ApprovalChain(
                action_type="deploy",
                approvers=("lead", "lead"),
            )

    def test_duplicate_approvers_case_insensitive(self) -> None:
        """Reject approvers that differ only by case."""
        with pytest.raises(ValidationError, match="Duplicate approvers"):
            ApprovalChain(
                action_type="deploy",
                approvers=("Lead", "lead"),
            )

    def test_frozen(self) -> None:
        """Ensure ApprovalChain is immutable."""
        c = ApprovalChain(action_type="deploy", approvers=("lead",))
        with pytest.raises(ValidationError):
            c.action_type = "other"  # type: ignore[misc]


# ── DepartmentPolicies ────────────────────────────────────────────


@pytest.mark.unit
class TestDepartmentPolicies:
    """Tests for DepartmentPolicies validation."""

    def test_defaults(self) -> None:
        """Verify default values."""
        p = DepartmentPolicies()
        assert isinstance(p.review_requirements, ReviewRequirements)
        assert p.approval_chains == ()

    def test_unique_action_types_validated(self) -> None:
        """Reject duplicate action_types across approval chains."""
        with pytest.raises(ValidationError, match="Duplicate action types"):
            DepartmentPolicies(
                approval_chains=(
                    ApprovalChain(action_type="deploy", approvers=("lead",)),
                    ApprovalChain(action_type="deploy", approvers=("dir",)),
                ),
            )


# ── WorkflowHandoff ──────────────────────────────────────────────


@pytest.mark.unit
class TestWorkflowHandoff:
    """Tests for WorkflowHandoff validation."""

    def test_valid(self) -> None:
        """Accept a valid handoff."""
        h = WorkflowHandoff(
            from_department="eng",
            to_department="qa",
            trigger="code_complete",
            artifacts=("build_artifact",),
        )
        assert h.from_department == "eng"
        assert len(h.artifacts) == 1

    def test_same_department_rejected(self) -> None:
        """Reject handoff within the same department."""
        with pytest.raises(ValidationError, match="different departments"):
            WorkflowHandoff(
                from_department="eng",
                to_department="eng",
                trigger="test",
            )

    def test_same_department_case_insensitive(self) -> None:
        """Same-department check is case-insensitive."""
        with pytest.raises(ValidationError, match="different departments"):
            WorkflowHandoff(
                from_department="Eng",
                to_department="eng",
                trigger="test",
            )


# ── EscalationPath ───────────────────────────────────────────────


@pytest.mark.unit
class TestEscalationPath:
    """Tests for EscalationPath validation."""

    def test_valid(self) -> None:
        """Accept a valid escalation path."""
        e = EscalationPath(
            from_department="eng",
            to_department="exec",
            condition="critical",
            priority_boost=2,
        )
        assert e.priority_boost == 2

    def test_priority_boost_boundaries(self) -> None:
        """Accept boundary values for priority_boost."""
        low = EscalationPath(
            from_department="a",
            to_department="b",
            condition="c",
            priority_boost=0,
        )
        high = EscalationPath(
            from_department="a",
            to_department="b",
            condition="c",
            priority_boost=3,
        )
        assert low.priority_boost == 0
        assert high.priority_boost == 3

    def test_priority_boost_above_3_rejected(self) -> None:
        """Reject priority_boost above 3."""
        with pytest.raises(ValidationError):
            EscalationPath(
                from_department="a",
                to_department="b",
                condition="c",
                priority_boost=4,
            )

    def test_priority_boost_negative_rejected(self) -> None:
        """Reject negative priority_boost."""
        with pytest.raises(ValidationError):
            EscalationPath(
                from_department="a",
                to_department="b",
                condition="c",
                priority_boost=-1,
            )

    def test_same_department_rejected(self) -> None:
        """Reject escalation within the same department."""
        with pytest.raises(ValidationError, match="different departments"):
            EscalationPath(
                from_department="eng",
                to_department="eng",
                condition="test",
            )

    def test_same_department_case_insensitive(self) -> None:
        """Same-department check is case-insensitive."""
        with pytest.raises(ValidationError, match="different departments"):
            EscalationPath(
                from_department="Eng",
                to_department="eng",
                condition="test",
            )


# ── Department additions ──────────────────────────────────────────


@pytest.mark.unit
class TestDepartmentExtended:
    """Tests for Department with reporting_lines and policies."""

    def test_with_reporting_lines(self) -> None:
        """Accept department with reporting lines."""
        dept = Department(
            name="eng",
            head="cto",
            reporting_lines=(ReportingLine(subordinate="dev", supervisor="lead"),),
        )
        assert len(dept.reporting_lines) == 1

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

    def test_backward_compatible_defaults(self) -> None:
        """Default reporting_lines and policies for backward compatibility."""
        dept = Department(name="eng", head="cto")
        assert dept.reporting_lines == ()
        assert isinstance(dept.policies, DepartmentPolicies)

    def test_duplicate_subordinates_rejected(self) -> None:
        """Reject duplicate subordinates in reporting lines."""
        with pytest.raises(ValidationError, match="Duplicate subordinates"):
            Department(
                name="eng",
                head="cto",
                reporting_lines=(
                    ReportingLine(subordinate="dev", supervisor="lead"),
                    ReportingLine(subordinate="dev", supervisor="manager"),
                ),
            )

    def test_duplicate_subordinates_case_insensitive(self) -> None:
        """Reject subordinates that differ only by case."""
        with pytest.raises(ValidationError, match="Duplicate subordinates"):
            Department(
                name="eng",
                head="cto",
                reporting_lines=(
                    ReportingLine(subordinate="Alice", supervisor="lead"),
                    ReportingLine(subordinate="alice", supervisor="manager"),
                ),
            )

    def test_duplicate_subordinates_whitespace_insensitive(self) -> None:
        """Reject subordinates that differ only by surrounding whitespace."""
        with pytest.raises(ValidationError, match="Duplicate subordinates"):
            Department(
                name="eng",
                head="cto",
                reporting_lines=(
                    ReportingLine(subordinate="Alice", supervisor="lead"),
                    ReportingLine(subordinate=" Alice ", supervisor="manager"),
                ),
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
