"""Tests for budget-specific enumerations."""

import pytest

from synthorg.budget.enums import BudgetAlertLevel

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestBudgetAlertLevel:
    """Tests for BudgetAlertLevel enum."""

    def test_all_members_exist(self) -> None:
        """Verify all four members are defined."""
        members = set(BudgetAlertLevel)
        assert len(members) == 4
        assert BudgetAlertLevel.NORMAL in members
        assert BudgetAlertLevel.WARNING in members
        assert BudgetAlertLevel.CRITICAL in members
        assert BudgetAlertLevel.HARD_STOP in members

    def test_values_are_strings(self) -> None:
        """Verify StrEnum produces string values."""
        assert BudgetAlertLevel.NORMAL.value == "normal"
        assert BudgetAlertLevel.WARNING.value == "warning"
        assert BudgetAlertLevel.CRITICAL.value == "critical"
        assert BudgetAlertLevel.HARD_STOP.value == "hard_stop"

    def test_membership(self) -> None:
        """Verify string-based membership check works."""
        assert "normal" in BudgetAlertLevel.__members__.values()
        assert "warning" in BudgetAlertLevel.__members__.values()
