"""Tests for company workflow models (reviews, approvals, handoffs, escalations)."""

import pytest
from pydantic import ValidationError

from synthorg.core.company_departments import (
    ApprovalChain,
    DepartmentPolicies,
    ReviewRequirements,
)
from synthorg.core.company_handoffs import EscalationPath, WorkflowHandoff

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

    @pytest.mark.parametrize(
        "approvers",
        [("lead", "lead"), ("Lead", "lead")],
        ids=["exact-duplicate", "case-insensitive"],
    )
    def test_duplicate_approvers_rejected(
        self,
        approvers: tuple[str, str],
    ) -> None:
        """Reject duplicate approvers (exact or case-insensitive)."""
        with pytest.raises(ValidationError, match="Duplicate approvers"):
            ApprovalChain(action_type="deploy", approvers=approvers)

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
                    ApprovalChain(
                        action_type="deploy",
                        approvers=("lead",),
                    ),
                    ApprovalChain(
                        action_type="deploy",
                        approvers=("dir",),
                    ),
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

    @pytest.mark.parametrize(
        ("from_dept", "to_dept"),
        [("eng", "eng"), ("Eng", "eng")],
        ids=["exact", "case-insensitive"],
    )
    def test_same_department_rejected(
        self,
        from_dept: str,
        to_dept: str,
    ) -> None:
        """Reject handoff within the same department."""
        with pytest.raises(ValidationError, match="different departments"):
            WorkflowHandoff(
                from_department=from_dept,
                to_department=to_dept,
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

    @pytest.mark.parametrize("boost", [4, -1])
    def test_priority_boost_invalid_rejected(self, boost: int) -> None:
        """Reject priority_boost outside 0-3 range."""
        with pytest.raises(ValidationError):
            EscalationPath(
                from_department="a",
                to_department="b",
                condition="c",
                priority_boost=boost,
            )

    @pytest.mark.parametrize(
        ("from_dept", "to_dept"),
        [("eng", "eng"), ("Eng", "eng")],
        ids=["exact", "case-insensitive"],
    )
    def test_same_department_rejected(
        self,
        from_dept: str,
        to_dept: str,
    ) -> None:
        """Reject escalation within the same department."""
        with pytest.raises(ValidationError, match="different departments"):
            EscalationPath(
                from_department=from_dept,
                to_department=to_dept,
                condition="test",
            )
