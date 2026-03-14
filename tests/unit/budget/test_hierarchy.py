"""Tests for budget hierarchy models."""

import pytest
from pydantic import ValidationError

from synthorg.budget.hierarchy import (
    BudgetHierarchy,
    DepartmentBudget,
    TeamBudget,
)

from .conftest import (
    BudgetHierarchyFactory,
    DepartmentBudgetFactory,
    TeamBudgetFactory,
)

pytestmark = pytest.mark.timeout(30)

# ── TeamBudget ────────────────────────────────────────────────────


@pytest.mark.unit
class TestTeamBudget:
    """Tests for TeamBudget validation, defaults, and immutability."""

    def test_valid(self) -> None:
        """Verify a valid team budget persists all fields."""
        tb = TeamBudget(team_name="Backend", budget_percent=40.0)
        assert tb.team_name == "Backend"
        assert tb.budget_percent == 40.0

    def test_defaults(self) -> None:
        """Verify default budget_percent is 0.0."""
        tb = TeamBudget(team_name="Frontend")
        assert tb.budget_percent == 0.0

    def test_empty_name_rejected(self) -> None:
        """Reject empty team name."""
        with pytest.raises(ValidationError):
            TeamBudget(team_name="")

    def test_whitespace_name_rejected(self) -> None:
        """Reject whitespace-only team name."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            TeamBudget(team_name="   ")

    def test_budget_percent_boundary_0(self) -> None:
        """Accept budget_percent at lower boundary (0.0)."""
        tb = TeamBudget(team_name="Test", budget_percent=0.0)
        assert tb.budget_percent == 0.0

    def test_budget_percent_boundary_100(self) -> None:
        """Accept budget_percent at upper boundary (100.0)."""
        tb = TeamBudget(team_name="Test", budget_percent=100.0)
        assert tb.budget_percent == 100.0

    def test_budget_percent_negative_rejected(self) -> None:
        """Reject negative budget_percent."""
        with pytest.raises(ValidationError):
            TeamBudget(team_name="Test", budget_percent=-1.0)

    def test_budget_percent_over_100_rejected(self) -> None:
        """Reject budget_percent above 100."""
        with pytest.raises(ValidationError):
            TeamBudget(team_name="Test", budget_percent=100.1)

    def test_frozen(self) -> None:
        """Ensure TeamBudget is immutable."""
        tb = TeamBudget(team_name="Test")
        with pytest.raises(ValidationError):
            tb.team_name = "Changed"  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid instance."""
        tb = TeamBudgetFactory.build()
        assert isinstance(tb, TeamBudget)


# ── DepartmentBudget ──────────────────────────────────────────────


@pytest.mark.unit
class TestDepartmentBudget:
    """Tests for DepartmentBudget validation, budget constraints, and immutability."""

    def test_valid(self) -> None:
        """Verify a valid department budget with teams."""
        db = DepartmentBudget(
            department_name="Engineering",
            budget_percent=50.0,
            teams=(
                TeamBudget(team_name="Backend", budget_percent=40.0),
                TeamBudget(team_name="Frontend", budget_percent=30.0),
            ),
        )
        assert db.department_name == "Engineering"
        assert db.budget_percent == 50.0
        assert len(db.teams) == 2

    def test_defaults(self) -> None:
        """Verify default budget_percent and empty teams."""
        db = DepartmentBudget(department_name="Test")
        assert db.budget_percent == 0.0
        assert db.teams == ()

    def test_empty_name_rejected(self) -> None:
        """Reject empty department name."""
        with pytest.raises(ValidationError):
            DepartmentBudget(department_name="")

    def test_whitespace_name_rejected(self) -> None:
        """Reject whitespace-only department name."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            DepartmentBudget(department_name="   ")

    def test_duplicate_team_names_rejected(self) -> None:
        """Reject duplicate team names within a department."""
        with pytest.raises(ValidationError, match="Duplicate team names"):
            DepartmentBudget(
                department_name="Eng",
                teams=(
                    TeamBudget(team_name="Backend", budget_percent=30.0),
                    TeamBudget(team_name="Backend", budget_percent=20.0),
                ),
            )

    def test_team_budget_sum_at_100_accepted(self) -> None:
        """Accept teams whose budget_percent sums to exactly 100."""
        db = DepartmentBudget(
            department_name="Eng",
            teams=(
                TeamBudget(team_name="A", budget_percent=60.0),
                TeamBudget(team_name="B", budget_percent=40.0),
            ),
        )
        total = sum(t.budget_percent for t in db.teams)
        assert total == 100.0

    def test_team_budget_sum_under_100_accepted(self) -> None:
        """Accept teams whose budget_percent sums to less than 100."""
        db = DepartmentBudget(
            department_name="Eng",
            teams=(
                TeamBudget(team_name="A", budget_percent=30.0),
                TeamBudget(team_name="B", budget_percent=20.0),
            ),
        )
        total = sum(t.budget_percent for t in db.teams)
        assert total == 50.0

    def test_team_budget_sum_over_100_rejected(self) -> None:
        """Reject teams whose budget_percent exceeds 100."""
        with pytest.raises(ValidationError, match="exceeding 100%"):
            DepartmentBudget(
                department_name="Eng",
                teams=(
                    TeamBudget(team_name="A", budget_percent=60.0),
                    TeamBudget(team_name="B", budget_percent=50.0),
                ),
            )

    def test_team_budget_float_precision_accepted(self) -> None:
        """Float artifacts (33.33+33.33+33.34) should not cause false rejections."""
        db = DepartmentBudget(
            department_name="Eng",
            teams=(
                TeamBudget(team_name="A", budget_percent=33.33),
                TeamBudget(team_name="B", budget_percent=33.33),
                TeamBudget(team_name="C", budget_percent=33.34),
            ),
        )
        assert len(db.teams) == 3

    def test_frozen(self) -> None:
        """Ensure DepartmentBudget is immutable."""
        db = DepartmentBudget(department_name="Test")
        with pytest.raises(ValidationError):
            db.department_name = "Changed"  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid instance."""
        db = DepartmentBudgetFactory.build()
        assert isinstance(db, DepartmentBudget)


# ── BudgetHierarchy ───────────────────────────────────────────────


@pytest.mark.unit
class TestBudgetHierarchy:
    """Tests for BudgetHierarchy validation, budget constraints, and immutability."""

    def test_valid(self, sample_budget_hierarchy: BudgetHierarchy) -> None:
        """Verify fixture-provided hierarchy has expected fields."""
        assert sample_budget_hierarchy.total_monthly == 100.0
        assert len(sample_budget_hierarchy.departments) == 2

    def test_defaults(self) -> None:
        """Verify default empty departments."""
        bh = BudgetHierarchy(total_monthly=100.0)
        assert bh.departments == ()

    def test_zero_budget_accepted(self) -> None:
        """Accept zero total_monthly budget."""
        bh = BudgetHierarchy(total_monthly=0.0)
        assert bh.total_monthly == 0.0

    def test_negative_budget_rejected(self) -> None:
        """Reject negative total_monthly."""
        with pytest.raises(ValidationError):
            BudgetHierarchy(total_monthly=-1.0)

    def test_duplicate_department_names_rejected(self) -> None:
        """Reject duplicate department names."""
        with pytest.raises(ValidationError, match="Duplicate department names"):
            BudgetHierarchy(
                total_monthly=100.0,
                departments=(
                    DepartmentBudget(department_name="Eng", budget_percent=30.0),
                    DepartmentBudget(department_name="Eng", budget_percent=20.0),
                ),
            )

    def test_department_budget_sum_at_100_accepted(self) -> None:
        """Accept departments whose budget_percent sums to exactly 100."""
        bh = BudgetHierarchy(
            total_monthly=100.0,
            departments=(
                DepartmentBudget(department_name="A", budget_percent=60.0),
                DepartmentBudget(department_name="B", budget_percent=40.0),
            ),
        )
        total = sum(d.budget_percent for d in bh.departments)
        assert total == 100.0

    def test_department_budget_sum_under_100_accepted(self) -> None:
        """Accept departments whose budget_percent sums to less than 100."""
        bh = BudgetHierarchy(
            total_monthly=100.0,
            departments=(
                DepartmentBudget(department_name="A", budget_percent=50.0),
                DepartmentBudget(department_name="B", budget_percent=30.0),
            ),
        )
        total = sum(d.budget_percent for d in bh.departments)
        assert total == 80.0

    def test_department_budget_sum_over_100_rejected(self) -> None:
        """Reject departments whose budget_percent exceeds 100."""
        with pytest.raises(ValidationError, match="exceeding 100%"):
            BudgetHierarchy(
                total_monthly=100.0,
                departments=(
                    DepartmentBudget(department_name="A", budget_percent=60.0),
                    DepartmentBudget(department_name="B", budget_percent=50.0),
                ),
            )

    def test_department_budget_float_precision_accepted(self) -> None:
        """Float artifacts should not cause false rejections."""
        bh = BudgetHierarchy(
            total_monthly=100.0,
            departments=(
                DepartmentBudget(department_name="A", budget_percent=33.33),
                DepartmentBudget(department_name="B", budget_percent=33.33),
                DepartmentBudget(department_name="C", budget_percent=33.34),
            ),
        )
        assert len(bh.departments) == 3

    def test_frozen(self) -> None:
        """Ensure BudgetHierarchy is immutable."""
        bh = BudgetHierarchy(total_monthly=100.0)
        with pytest.raises(ValidationError):
            bh.total_monthly = 200.0  # type: ignore[misc]

    def test_json_roundtrip(self, sample_budget_hierarchy: BudgetHierarchy) -> None:
        """Verify JSON serialization and deserialization preserves fields."""
        json_str = sample_budget_hierarchy.model_dump_json()
        restored = BudgetHierarchy.model_validate_json(json_str)
        assert restored.total_monthly == sample_budget_hierarchy.total_monthly
        assert len(restored.departments) == len(sample_budget_hierarchy.departments)

    def test_factory(self) -> None:
        """Verify factory produces a valid instance."""
        bh = BudgetHierarchyFactory.build()
        assert isinstance(bh, BudgetHierarchy)
