"""Tests for department policy configuration in built-in templates."""

import pytest

from synthorg.config.schema import RootConfig
from synthorg.core.company import ApprovalChain, Department
from synthorg.templates.loader import load_template
from synthorg.templates.renderer import render_template

# -- Helpers ---------------------------------------------------------------


def _get_dept(config: RootConfig, name: str) -> Department:
    """Find a department by name; calls pytest.fail if absent."""
    for d in config.departments:
        if d.name == name:
            return d
    available = [d.name for d in config.departments]
    pytest.fail(f"Department {name!r} not found; available: {available}")


def _get_chain(dept: Department, action_type: str) -> ApprovalChain:
    """Find an approval chain by action type; calls pytest.fail if absent."""
    for c in dept.policies.approval_chains:
        if c.action_type == action_type:
            return c
    existing = [c.action_type for c in dept.policies.approval_chains]
    pytest.fail(
        f"No approval chain {action_type!r} in department "
        f"{dept.name!r}; existing: {existing}"
    )


def _render(name: str) -> RootConfig:
    """Load and render a built-in template by name."""
    return render_template(load_template(name))


# -- Tests -----------------------------------------------------------------


@pytest.mark.unit
class TestBuiltinDepartmentPolicies:
    """Verify review requirements and approval chains across built-in templates."""

    # -- engineering review requirements (parametrized) --------------------

    @pytest.mark.parametrize(
        ("template_name", "expected_reviewers"),
        [
            ("dev_shop", 1),
            ("product_team", 1),
            ("agency", 1),
            ("full_company", 2),
        ],
    )
    def test_engineering_review_requirements(
        self,
        template_name: str,
        expected_reviewers: int,
    ) -> None:
        config = _render(template_name)
        eng = _get_dept(config, "engineering")
        assert eng.policies.review_requirements.min_reviewers == expected_reviewers

    # -- dev_shop ----------------------------------------------------------

    def test_dev_shop_qa_test_coverage_chain(self) -> None:
        qa = _get_dept(_render("dev_shop"), "quality_assurance")
        chain = _get_chain(qa, "test_coverage")
        assert chain.approvers == ("QA Lead",)

    def test_dev_shop_operations_default_policies(self) -> None:
        ops = _get_dept(_render("dev_shop"), "operations")
        assert ops.policies.approval_chains == ()

    # -- product_team ------------------------------------------------------

    def test_product_team_design_review_chain(self) -> None:
        design = _get_dept(_render("product_team"), "design")
        chain = _get_chain(design, "design_review")
        assert chain.approvers == ("UX Designer",)

    # -- agency ------------------------------------------------------------

    def test_agency_operations_client_approval_chain(self) -> None:
        ops = _get_dept(_render("agency"), "operations")
        chain = _get_chain(ops, "client_approval")
        assert chain.approvers == ("Project Manager",)

    # -- full_company ------------------------------------------------------

    def test_full_company_engineering_code_review_chain(self) -> None:
        eng = _get_dept(_render("full_company"), "engineering")
        chain = _get_chain(eng, "code_review")
        assert chain.approvers == ("Software Architect", "CTO")

    def test_full_company_security_review_chain(self) -> None:
        sec = _get_dept(_render("full_company"), "security")
        chain = _get_chain(sec, "security_review")
        assert chain.approvers == ("Security Engineer", "CTO")

    def test_full_company_operations_change_management_chain(self) -> None:
        ops = _get_dept(_render("full_company"), "operations")
        chain = _get_chain(ops, "change_management")
        assert chain.approvers == ("COO",)
