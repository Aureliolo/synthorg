"""Tests for spending summary models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.spending_summary import (
    AgentSpending,
    DepartmentSpending,
    PeriodSpending,
    SpendingSummary,
)

from .conftest import (
    AgentSpendingFactory,
    DepartmentSpendingFactory,
    PeriodSpendingFactory,
    SpendingSummaryFactory,
)

pytestmark = pytest.mark.timeout(30)

# ── PeriodSpending ────────────────────────────────────────────────


@pytest.mark.unit
class TestPeriodSpending:
    """Tests for PeriodSpending validation, defaults, and immutability."""

    def test_valid(self) -> None:
        """Verify a valid period spending instance."""
        ps = PeriodSpending(
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 3, 1, tzinfo=UTC),
            total_cost_usd=50.0,
            record_count=100,
        )
        assert ps.total_cost_usd == 50.0
        assert ps.record_count == 100

    def test_defaults(self) -> None:
        """Verify zero defaults for aggregation fields."""
        ps = PeriodSpending(
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 3, 1, tzinfo=UTC),
        )
        assert ps.total_cost_usd == 0.0
        assert ps.total_input_tokens == 0
        assert ps.total_output_tokens == 0
        assert ps.record_count == 0

    def test_start_after_end_rejected(self) -> None:
        """Reject start after end."""
        with pytest.raises(ValidationError, match="must be before end"):
            PeriodSpending(
                start=datetime(2026, 3, 1, tzinfo=UTC),
                end=datetime(2026, 2, 1, tzinfo=UTC),
            )

    def test_start_equals_end_rejected(self) -> None:
        """Reject start equal to end."""
        ts = datetime(2026, 2, 1, tzinfo=UTC)
        with pytest.raises(ValidationError, match="must be before end"):
            PeriodSpending(start=ts, end=ts)

    def test_frozen(self) -> None:
        """Ensure PeriodSpending is immutable."""
        ps = PeriodSpending(
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 3, 1, tzinfo=UTC),
        )
        with pytest.raises(ValidationError):
            ps.total_cost_usd = 999.0  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid instance."""
        ps = PeriodSpendingFactory.build()
        assert isinstance(ps, PeriodSpending)


# ── AgentSpending ─────────────────────────────────────────────────


@pytest.mark.unit
class TestAgentSpending:
    """Tests for AgentSpending validation, defaults, and immutability."""

    def test_valid(self) -> None:
        """Verify a valid agent spending instance."""
        a = AgentSpending(
            agent_id="sarah_chen",
            total_cost_usd=40.0,
            record_count=80,
        )
        assert a.agent_id == "sarah_chen"
        assert a.total_cost_usd == 40.0

    def test_defaults(self) -> None:
        """Verify zero defaults for aggregation fields."""
        a = AgentSpending(agent_id="test")
        assert a.total_cost_usd == 0.0
        assert a.total_input_tokens == 0
        assert a.total_output_tokens == 0
        assert a.record_count == 0

    def test_empty_agent_id_rejected(self) -> None:
        """Reject empty agent_id."""
        with pytest.raises(ValidationError):
            AgentSpending(agent_id="")

    def test_whitespace_agent_id_rejected(self) -> None:
        """Reject whitespace-only agent_id."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            AgentSpending(agent_id="   ")

    def test_frozen(self) -> None:
        """Ensure AgentSpending is immutable."""
        a = AgentSpending(agent_id="test")
        with pytest.raises(ValidationError):
            a.agent_id = "changed"  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid instance."""
        a = AgentSpendingFactory.build()
        assert isinstance(a, AgentSpending)


# ── DepartmentSpending ────────────────────────────────────────────


@pytest.mark.unit
class TestDepartmentSpending:
    """Tests for DepartmentSpending validation, defaults, and immutability."""

    def test_valid(self) -> None:
        """Verify a valid department spending instance."""
        d = DepartmentSpending(
            department_name="Engineering",
            total_cost_usd=75.0,
            record_count=150,
        )
        assert d.department_name == "Engineering"
        assert d.total_cost_usd == 75.0

    def test_defaults(self) -> None:
        """Verify zero defaults for aggregation fields."""
        d = DepartmentSpending(department_name="Test")
        assert d.total_cost_usd == 0.0
        assert d.total_input_tokens == 0
        assert d.total_output_tokens == 0
        assert d.record_count == 0

    def test_empty_name_rejected(self) -> None:
        """Reject empty department name."""
        with pytest.raises(ValidationError):
            DepartmentSpending(department_name="")

    def test_whitespace_name_rejected(self) -> None:
        """Reject whitespace-only department name."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            DepartmentSpending(department_name="   ")

    def test_frozen(self) -> None:
        """Ensure DepartmentSpending is immutable."""
        d = DepartmentSpending(department_name="Test")
        with pytest.raises(ValidationError):
            d.department_name = "Changed"  # type: ignore[misc]

    def test_factory(self) -> None:
        """Verify factory produces a valid instance."""
        d = DepartmentSpendingFactory.build()
        assert isinstance(d, DepartmentSpending)


# ── SpendingSummary ───────────────────────────────────────────────


@pytest.mark.unit
class TestSpendingSummary:
    """Tests for SpendingSummary validation, defaults, and immutability."""

    def test_valid(self, sample_spending_summary: SpendingSummary) -> None:
        """Verify fixture-provided summary has expected fields."""
        assert sample_spending_summary.budget_total_monthly == 100.0
        assert sample_spending_summary.budget_used_percent == 75.5
        assert sample_spending_summary.alert_level is BudgetAlertLevel.WARNING
        assert len(sample_spending_summary.by_agent) == 2
        assert len(sample_spending_summary.by_department) == 1

    def test_defaults(self) -> None:
        """Verify default values for optional fields."""
        summary = SpendingSummary(
            period=PeriodSpending(
                start=datetime(2026, 2, 1, tzinfo=UTC),
                end=datetime(2026, 3, 1, tzinfo=UTC),
            ),
        )
        assert summary.by_agent == ()
        assert summary.by_department == ()
        assert summary.budget_total_monthly == 0.0
        assert summary.budget_used_percent == 0.0
        assert summary.alert_level is BudgetAlertLevel.NORMAL

    def test_duplicate_agent_ids_rejected(self) -> None:
        """Reject duplicate agent_id values in by_agent."""
        with pytest.raises(ValidationError, match="Duplicate agent_id"):
            SpendingSummary(
                period=PeriodSpending(
                    start=datetime(2026, 2, 1, tzinfo=UTC),
                    end=datetime(2026, 3, 1, tzinfo=UTC),
                ),
                by_agent=(
                    AgentSpending(agent_id="alice", total_cost_usd=10.0),
                    AgentSpending(agent_id="alice", total_cost_usd=20.0),
                ),
            )

    def test_duplicate_department_names_rejected(self) -> None:
        """Reject duplicate department_name values in by_department."""
        with pytest.raises(ValidationError, match="Duplicate department_name"):
            SpendingSummary(
                period=PeriodSpending(
                    start=datetime(2026, 2, 1, tzinfo=UTC),
                    end=datetime(2026, 3, 1, tzinfo=UTC),
                ),
                by_department=(
                    DepartmentSpending(department_name="Eng", total_cost_usd=10.0),
                    DepartmentSpending(department_name="Eng", total_cost_usd=20.0),
                ),
            )

    def test_all_alert_levels_accepted(self) -> None:
        """Verify all BudgetAlertLevel values work."""
        for level in BudgetAlertLevel:
            summary = SpendingSummary(
                period=PeriodSpending(
                    start=datetime(2026, 2, 1, tzinfo=UTC),
                    end=datetime(2026, 3, 1, tzinfo=UTC),
                ),
                alert_level=level,
            )
            assert summary.alert_level is level

    def test_frozen(self) -> None:
        """Ensure SpendingSummary is immutable."""
        summary = SpendingSummary(
            period=PeriodSpending(
                start=datetime(2026, 2, 1, tzinfo=UTC),
                end=datetime(2026, 3, 1, tzinfo=UTC),
            ),
        )
        with pytest.raises(ValidationError):
            summary.budget_total_monthly = 999.0  # type: ignore[misc]

    def test_json_roundtrip(self, sample_spending_summary: SpendingSummary) -> None:
        """Verify full serialization roundtrip."""
        json_str = sample_spending_summary.model_dump_json()
        restored = SpendingSummary.model_validate_json(json_str)
        assert (
            restored.budget_total_monthly
            == sample_spending_summary.budget_total_monthly
        )
        assert restored.alert_level == sample_spending_summary.alert_level
        assert len(restored.by_agent) == len(sample_spending_summary.by_agent)
        assert len(restored.by_department) == len(sample_spending_summary.by_department)

    def test_factory(self) -> None:
        """Verify factory produces a valid instance."""
        summary = SpendingSummaryFactory.build()
        assert isinstance(summary, SpendingSummary)
