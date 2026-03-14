"""Tests for HierarchyResolver."""

import pytest

from synthorg.communication.delegation.hierarchy import (
    HierarchyResolver,
)
from synthorg.communication.errors import HierarchyResolutionError
from synthorg.core.company import (
    Company,
    CompanyConfig,
    Department,
    ReportingLine,
    Team,
)

pytestmark = pytest.mark.timeout(30)


def _make_company(
    departments: tuple[Department, ...] = (),
) -> Company:
    """Create a minimal Company with given departments."""
    return Company(
        name="Test Corp",
        departments=departments,
        config=CompanyConfig(budget_monthly=100.0),
    )


def _eng_department() -> Department:
    """Engineering department with 2 teams and reporting lines."""
    return Department(
        name="Engineering",
        head="cto",
        budget_percent=60.0,
        teams=(
            Team(
                name="backend",
                lead="backend_lead",
                members=("sr_dev", "jr_dev"),
            ),
            Team(
                name="frontend",
                lead="frontend_lead",
                members=("ui_dev",),
            ),
        ),
    )


def _qa_department() -> Department:
    """QA department with one team."""
    return Department(
        name="QA",
        head="qa_head",
        budget_percent=20.0,
        teams=(
            Team(
                name="testing",
                lead="qa_lead",
                members=("qa_eng",),
            ),
        ),
    )


@pytest.mark.unit
class TestHierarchyResolverConstruction:
    def test_builds_from_single_department(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_supervisor("backend_lead") == "cto"

    def test_builds_from_multi_department(self) -> None:
        company = _make_company(departments=(_eng_department(), _qa_department()))
        resolver = HierarchyResolver(company)
        assert resolver.get_supervisor("qa_lead") == "qa_head"

    def test_empty_company(self) -> None:
        company = _make_company()
        resolver = HierarchyResolver(company)
        assert resolver.get_supervisor("anyone") is None


@pytest.mark.unit
class TestGetSupervisor:
    def test_team_member_supervisor_is_lead(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_supervisor("sr_dev") == "backend_lead"
        assert resolver.get_supervisor("jr_dev") == "backend_lead"

    def test_team_lead_supervisor_is_dept_head(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_supervisor("backend_lead") == "cto"
        assert resolver.get_supervisor("frontend_lead") == "cto"

    def test_dept_head_has_no_supervisor(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_supervisor("cto") is None

    def test_unknown_agent_returns_none(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_supervisor("nonexistent") is None


@pytest.mark.unit
class TestGetDirectReports:
    def test_dept_head_reports(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        reports = resolver.get_direct_reports("cto")
        assert "backend_lead" in reports
        assert "frontend_lead" in reports

    def test_team_lead_reports(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        reports = resolver.get_direct_reports("backend_lead")
        assert "sr_dev" in reports
        assert "jr_dev" in reports

    def test_leaf_agent_no_reports(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_direct_reports("jr_dev") == ()

    def test_unknown_agent_empty(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_direct_reports("nonexistent") == ()


@pytest.mark.unit
class TestIsDirectReport:
    def test_direct_report_true(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.is_direct_report("backend_lead", "sr_dev")

    def test_not_direct_report_false(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert not resolver.is_direct_report("cto", "sr_dev")


@pytest.mark.unit
class TestIsSubordinate:
    def test_direct_subordinate(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.is_subordinate("backend_lead", "sr_dev")

    def test_skip_level_subordinate(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.is_subordinate("cto", "jr_dev")

    def test_not_subordinate(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert not resolver.is_subordinate("sr_dev", "cto")

    def test_same_agent_not_subordinate(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert not resolver.is_subordinate("cto", "cto")

    def test_cross_department_not_subordinate(self) -> None:
        company = _make_company(departments=(_eng_department(), _qa_department()))
        resolver = HierarchyResolver(company)
        assert not resolver.is_subordinate("cto", "qa_eng")


@pytest.mark.unit
class TestGetAncestors:
    def test_leaf_agent_ancestors(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        ancestors = resolver.get_ancestors("jr_dev")
        assert ancestors == ("backend_lead", "cto")

    def test_team_lead_ancestors(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        ancestors = resolver.get_ancestors("backend_lead")
        assert ancestors == ("cto",)

    def test_dept_head_no_ancestors(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_ancestors("cto") == ()

    def test_unknown_agent_no_ancestors(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_ancestors("nonexistent") == ()


@pytest.mark.unit
class TestGetDelegationDepth:
    def test_direct_depth_one(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_delegation_depth("backend_lead", "sr_dev") == 1

    def test_skip_level_depth_two(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_delegation_depth("cto", "jr_dev") == 2

    def test_not_below_returns_none(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_delegation_depth("jr_dev", "cto") is None

    def test_same_agent_returns_none(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_delegation_depth("cto", "cto") is None


@pytest.mark.unit
class TestExplicitReportingLines:
    def test_reporting_line_overrides_team_structure(self) -> None:
        """Explicit reporting line takes precedence over team lead."""
        dept = Department(
            name="Engineering",
            head="cto",
            budget_percent=50.0,
            teams=(
                Team(
                    name="backend",
                    lead="backend_lead",
                    members=("sr_dev",),
                ),
            ),
            reporting_lines=(
                ReportingLine(
                    subordinate="sr_dev",
                    supervisor="cto",
                ),
            ),
        )
        company = _make_company(departments=(dept,))
        resolver = HierarchyResolver(company)
        # sr_dev should report to cto, not backend_lead
        assert resolver.get_supervisor("sr_dev") == "cto"


@pytest.mark.unit
class TestEdgeCases:
    def test_lead_in_members_list_not_duplicated(self) -> None:
        """Team lead appearing in members should not be registered twice."""
        dept = Department(
            name="Engineering",
            head="cto",
            budget_percent=50.0,
            teams=(
                Team(
                    name="backend",
                    lead="backend_lead",
                    members=("backend_lead", "dev1"),
                ),
            ),
        )
        company = _make_company(departments=(dept,))
        resolver = HierarchyResolver(company)
        assert resolver.get_supervisor("backend_lead") == "cto"
        reports = resolver.get_direct_reports("backend_lead")
        assert reports.count("dev1") == 1
        assert "backend_lead" not in reports

    def test_multi_team_lead_keeps_first_supervisor(self) -> None:
        """Agent leading two teams keeps the first team's dept head."""
        dept = Department(
            name="Engineering",
            head="cto",
            budget_percent=50.0,
            teams=(
                Team(name="t1", lead="shared_lead", members=("dev1",)),
                Team(name="t2", lead="shared_lead", members=("dev2",)),
            ),
        )
        company = _make_company(departments=(dept,))
        resolver = HierarchyResolver(company)
        assert resolver.get_supervisor("shared_lead") == "cto"
        assert resolver.get_supervisor("dev1") == "shared_lead"
        assert resolver.get_supervisor("dev2") == "shared_lead"

    def test_dept_head_as_team_lead_no_self_cycle(self) -> None:
        """Department head also leading a team should not create a self-cycle."""
        dept = Department(
            name="Engineering",
            head="cto",
            budget_percent=50.0,
            teams=(Team(name="core", lead="cto", members=("dev1",)),),
        )
        company = _make_company(departments=(dept,))
        resolver = HierarchyResolver(company)
        # cto should not be their own supervisor
        assert resolver.get_supervisor("cto") is None
        assert resolver.get_supervisor("dev1") == "cto"

    def test_member_with_prior_supervisor_keeps_first(self) -> None:
        """Member already assigned to a supervisor is not re-assigned."""
        dept = Department(
            name="Engineering",
            head="cto",
            budget_percent=50.0,
            teams=(
                Team(name="t1", lead="lead1", members=("dev1",)),
                Team(name="t2", lead="lead2", members=("dev1",)),
            ),
        )
        company = _make_company(departments=(dept,))
        resolver = HierarchyResolver(company)
        # dev1 seen first under lead1
        assert resolver.get_supervisor("dev1") == "lead1"

    def test_redundant_reporting_line_no_duplicate(self) -> None:
        """Explicit line matching inferred relationship doesn't duplicate."""
        dept = Department(
            name="Engineering",
            head="cto",
            budget_percent=50.0,
            teams=(Team(name="backend", lead="lead1", members=("dev1",)),),
            reporting_lines=(ReportingLine(subordinate="dev1", supervisor="lead1"),),
        )
        company = _make_company(departments=(dept,))
        resolver = HierarchyResolver(company)
        assert resolver.get_supervisor("dev1") == "lead1"
        reports = resolver.get_direct_reports("lead1")
        assert reports.count("dev1") == 1


@pytest.mark.unit
class TestGetLowestCommonManager:
    def test_same_team_lcm_is_lead(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_lowest_common_manager("sr_dev", "jr_dev") == "backend_lead"

    def test_cross_team_lcm_is_dept_head(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_lowest_common_manager("sr_dev", "ui_dev") == "cto"

    def test_no_common_manager_returns_none(self) -> None:
        company = _make_company(departments=(_eng_department(), _qa_department()))
        resolver = HierarchyResolver(company)
        assert resolver.get_lowest_common_manager("cto", "qa_head") is None

    def test_agent_is_ancestor_of_other(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert (
            resolver.get_lowest_common_manager("backend_lead", "sr_dev")
            == "backend_lead"
        )

    def test_reverse_agent_is_ancestor(self) -> None:
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert (
            resolver.get_lowest_common_manager("sr_dev", "backend_lead")
            == "backend_lead"
        )

    def test_same_agent_returns_self(self) -> None:
        """Same agent as both arguments returns that agent."""
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_lowest_common_manager("sr_dev", "sr_dev") == "sr_dev"

    def test_same_unknown_agent_returns_none(self) -> None:
        """Unknown agent passed as both arguments returns None."""
        company = _make_company(departments=(_eng_department(),))
        resolver = HierarchyResolver(company)
        assert resolver.get_lowest_common_manager("unknown", "unknown") is None

    def test_cross_department_agents_under_same_root(self) -> None:
        """If eng/qa share a root, that root is LCM."""
        company = _make_company(departments=(_eng_department(), _qa_department()))
        resolver = HierarchyResolver(company)
        # sr_dev and qa_eng have no common manager (cto and qa_head are unlinked)
        assert resolver.get_lowest_common_manager("sr_dev", "qa_eng") is None


@pytest.mark.unit
class TestCycleDetection:
    def test_cycle_raises_hierarchy_error(self) -> None:
        dept = Department(
            name="Engineering",
            head="a",
            budget_percent=50.0,
            teams=(Team(name="t1", lead="b", members=("c",)),),
            reporting_lines=(ReportingLine(subordinate="a", supervisor="c"),),
        )
        company = _make_company(departments=(dept,))
        with pytest.raises(HierarchyResolutionError, match="Cycle"):
            HierarchyResolver(company)
