"""Unit tests for the CostTracker service."""

import asyncio
from datetime import UTC, datetime

import pytest
import structlog.testing

from ai_company.budget.config import BudgetAlertConfig, BudgetConfig
from ai_company.budget.enums import BudgetAlertLevel
from ai_company.budget.tracker import CostTracker
from ai_company.observability.events import BUDGET_RECORD_ADDED

from .conftest import make_cost_record

# ── TestCostTrackerRecord ────────────────────────────────────────


class TestCostTrackerRecord:
    """Tests for CostTracker.record()."""

    @pytest.mark.unit
    async def test_record_stores_record(self, cost_tracker: CostTracker) -> None:
        rec = make_cost_record(cost_usd=0.10)
        await cost_tracker.record(rec)

        assert cost_tracker.record_count == 1
        assert cost_tracker.total_cost_usd == 0.10

    @pytest.mark.unit
    async def test_record_multiple(self, cost_tracker: CostTracker) -> None:
        for i in range(3):
            await cost_tracker.record(make_cost_record(cost_usd=0.10 * (i + 1)))

        assert cost_tracker.record_count == 3
        assert cost_tracker.total_cost_usd == pytest.approx(0.60)

    @pytest.mark.unit
    async def test_record_concurrent_safety(self, cost_tracker: CostTracker) -> None:
        records = [make_cost_record(cost_usd=0.01) for _ in range(50)]
        await asyncio.gather(*(cost_tracker.record(r) for r in records))

        assert cost_tracker.record_count == 50
        assert cost_tracker.total_cost_usd == pytest.approx(0.50)

    @pytest.mark.unit
    async def test_records_snapshot_is_independent(
        self, cost_tracker: CostTracker
    ) -> None:
        await cost_tracker.record(make_cost_record())
        snap1 = await cost_tracker._snapshot()

        await cost_tracker.record(make_cost_record())
        snap2 = await cost_tracker._snapshot()

        assert len(snap1) == 1
        assert len(snap2) == 2

    @pytest.mark.unit
    async def test_record_logs_event(self, cost_tracker: CostTracker) -> None:
        rec = make_cost_record(agent_id="alice", cost_usd=0.05)
        with structlog.testing.capture_logs() as logs:
            await cost_tracker.record(rec)

        budget_logs = [
            entry for entry in logs if entry.get("event") == BUDGET_RECORD_ADDED
        ]
        assert len(budget_logs) == 1
        assert budget_logs[0]["agent_id"] == "alice"
        assert budget_logs[0]["cost_usd"] == 0.05


# ── TestCostTrackerQuery ─────────────────────────────────────────


class TestCostTrackerQuery:
    """Tests for query methods."""

    @pytest.mark.unit
    async def test_get_total_cost_empty(self, cost_tracker: CostTracker) -> None:
        assert await cost_tracker.get_total_cost() == 0.0

    @pytest.mark.unit
    async def test_get_total_cost_multiple(self, cost_tracker: CostTracker) -> None:
        await cost_tracker.record(make_cost_record(cost_usd=0.10))
        await cost_tracker.record(make_cost_record(cost_usd=0.20))
        await cost_tracker.record(make_cost_record(cost_usd=0.30))

        assert await cost_tracker.get_total_cost() == pytest.approx(0.60)

    @pytest.mark.unit
    async def test_get_total_cost_with_time_filter(
        self, cost_tracker: CostTracker
    ) -> None:
        t1 = datetime(2026, 2, 10, tzinfo=UTC)
        t2 = datetime(2026, 2, 15, tzinfo=UTC)
        t3 = datetime(2026, 2, 20, tzinfo=UTC)

        await cost_tracker.record(make_cost_record(cost_usd=0.10, timestamp=t1))
        await cost_tracker.record(make_cost_record(cost_usd=0.20, timestamp=t2))
        await cost_tracker.record(make_cost_record(cost_usd=0.30, timestamp=t3))

        # start inclusive, end exclusive
        result = await cost_tracker.get_total_cost(start=t2, end=t3)
        assert result == pytest.approx(0.20)

    @pytest.mark.unit
    async def test_get_agent_cost_found(self, cost_tracker: CostTracker) -> None:
        await cost_tracker.record(make_cost_record(agent_id="alice", cost_usd=0.10))
        await cost_tracker.record(make_cost_record(agent_id="bob", cost_usd=0.20))
        await cost_tracker.record(make_cost_record(agent_id="alice", cost_usd=0.30))

        assert await cost_tracker.get_agent_cost("alice") == pytest.approx(0.40)

    @pytest.mark.unit
    async def test_get_agent_cost_not_found(self, cost_tracker: CostTracker) -> None:
        await cost_tracker.record(make_cost_record(agent_id="alice", cost_usd=0.10))

        assert await cost_tracker.get_agent_cost("unknown") == 0.0

    @pytest.mark.unit
    async def test_get_agent_cost_with_time_filter(
        self, cost_tracker: CostTracker
    ) -> None:
        t1 = datetime(2026, 2, 10, tzinfo=UTC)
        t2 = datetime(2026, 2, 20, tzinfo=UTC)

        await cost_tracker.record(
            make_cost_record(agent_id="alice", cost_usd=0.10, timestamp=t1)
        )
        await cost_tracker.record(
            make_cost_record(agent_id="alice", cost_usd=0.20, timestamp=t2)
        )

        result = await cost_tracker.get_agent_cost(
            "alice",
            start=datetime(2026, 2, 15, tzinfo=UTC),
        )
        assert result == pytest.approx(0.20)

    @pytest.mark.unit
    async def test_get_record_count(self, cost_tracker: CostTracker) -> None:
        assert await cost_tracker.get_record_count() == 0
        await cost_tracker.record(make_cost_record())
        await cost_tracker.record(make_cost_record())
        assert await cost_tracker.get_record_count() == 2


# ── TestCostTrackerBuildSummary ──────────────────────────────────


class TestCostTrackerBuildSummary:
    """Tests for CostTracker.build_summary()."""

    _PERIOD_START = datetime(2026, 2, 1, tzinfo=UTC)
    _PERIOD_END = datetime(2026, 3, 1, tzinfo=UTC)

    @pytest.mark.unit
    async def test_summary_empty(self, cost_tracker: CostTracker) -> None:
        summary = await cost_tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )

        assert summary.period.total_cost_usd == 0.0
        assert summary.period.record_count == 0
        assert summary.by_agent == ()
        assert summary.by_department == ()

    @pytest.mark.unit
    async def test_summary_single_record(self, cost_tracker: CostTracker) -> None:
        await cost_tracker.record(
            make_cost_record(
                agent_id="alice",
                cost_usd=0.10,
                input_tokens=100,
                output_tokens=50,
            )
        )

        summary = await cost_tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )

        assert summary.period.total_cost_usd == pytest.approx(0.10)
        assert summary.period.total_input_tokens == 100
        assert summary.period.total_output_tokens == 50
        assert summary.period.record_count == 1
        assert len(summary.by_agent) == 1
        assert summary.by_agent[0].agent_id == "alice"

    @pytest.mark.unit
    async def test_summary_multiple_agents(self, cost_tracker: CostTracker) -> None:
        await cost_tracker.record(make_cost_record(agent_id="alice", cost_usd=0.10))
        await cost_tracker.record(make_cost_record(agent_id="bob", cost_usd=0.20))
        await cost_tracker.record(make_cost_record(agent_id="alice", cost_usd=0.15))

        summary = await cost_tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )

        assert len(summary.by_agent) == 2
        agents = {a.agent_id: a for a in summary.by_agent}
        assert agents["alice"].total_cost_usd == pytest.approx(0.25)
        assert agents["alice"].record_count == 2
        assert agents["bob"].total_cost_usd == pytest.approx(0.20)
        assert agents["bob"].record_count == 1

    @pytest.mark.unit
    async def test_summary_department_aggregation(
        self, cost_tracker: CostTracker
    ) -> None:
        # alice and bob → Engineering, carol → Product
        await cost_tracker.record(make_cost_record(agent_id="alice", cost_usd=0.10))
        await cost_tracker.record(make_cost_record(agent_id="bob", cost_usd=0.20))
        await cost_tracker.record(make_cost_record(agent_id="carol", cost_usd=0.30))

        summary = await cost_tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )

        depts = {d.department_name: d for d in summary.by_department}
        assert len(depts) == 2
        assert depts["Engineering"].total_cost_usd == pytest.approx(0.30)
        assert depts["Product"].total_cost_usd == pytest.approx(0.30)

    @pytest.mark.unit
    async def test_summary_department_resolver_returns_none(
        self, cost_tracker: CostTracker
    ) -> None:
        # "unknown_agent" is not in the department map → returns None
        await cost_tracker.record(
            make_cost_record(agent_id="unknown_agent", cost_usd=0.10)
        )

        summary = await cost_tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )

        assert summary.by_department == ()
        assert len(summary.by_agent) == 1

    @pytest.mark.unit
    async def test_summary_department_resolver_raises(self) -> None:
        def exploding_resolver(agent_id: str) -> str | None:
            msg = "resolver exploded"
            raise RuntimeError(msg)

        tracker = CostTracker(department_resolver=exploding_resolver)
        await tracker.record(make_cost_record(agent_id="alice", cost_usd=0.10))

        summary = await tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )

        assert summary.by_department == ()
        assert len(summary.by_agent) == 1

    @pytest.mark.unit
    async def test_summary_time_filter(self, cost_tracker: CostTracker) -> None:
        inside = datetime(2026, 2, 15, tzinfo=UTC)
        outside = datetime(2026, 3, 15, tzinfo=UTC)

        await cost_tracker.record(make_cost_record(cost_usd=0.10, timestamp=inside))
        await cost_tracker.record(make_cost_record(cost_usd=0.20, timestamp=outside))

        summary = await cost_tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )

        assert summary.period.total_cost_usd == pytest.approx(0.10)
        assert summary.period.record_count == 1

    @pytest.mark.unit
    async def test_summary_rounding_precision(self, cost_tracker: CostTracker) -> None:
        # Add many small costs that could cause float drift
        for _ in range(100):
            await cost_tracker.record(make_cost_record(cost_usd=0.001, input_tokens=1))

        summary = await cost_tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )

        assert summary.period.total_cost_usd == pytest.approx(0.10)


# ── TestCostTrackerAlertLevel ────────────────────────────────────


class TestCostTrackerAlertLevel:
    """Tests for alert level computation in build_summary."""

    _PERIOD_START = datetime(2026, 2, 1, tzinfo=UTC)
    _PERIOD_END = datetime(2026, 3, 1, tzinfo=UTC)

    @pytest.mark.unit
    async def test_alert_normal(self, cost_tracker: CostTracker) -> None:
        # 50% of 100 budget → NORMAL (threshold: warn_at=75)
        await cost_tracker.record(make_cost_record(cost_usd=50.0, input_tokens=50000))

        summary = await cost_tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )
        assert summary.alert_level == BudgetAlertLevel.NORMAL

    @pytest.mark.unit
    async def test_alert_warning(self, cost_tracker: CostTracker) -> None:
        # 80% of 100 budget → WARNING (threshold: warn_at=75, critical_at=90)
        await cost_tracker.record(make_cost_record(cost_usd=80.0, input_tokens=80000))

        summary = await cost_tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )
        assert summary.alert_level == BudgetAlertLevel.WARNING

    @pytest.mark.unit
    async def test_alert_critical(self, cost_tracker: CostTracker) -> None:
        # 92% of 100 budget → CRITICAL (threshold: critical_at=90, hard_stop_at=100)
        await cost_tracker.record(make_cost_record(cost_usd=92.0, input_tokens=92000))

        summary = await cost_tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )
        assert summary.alert_level == BudgetAlertLevel.CRITICAL

    @pytest.mark.unit
    async def test_alert_hard_stop(self, cost_tracker: CostTracker) -> None:
        # 100% of 100 budget → HARD_STOP
        await cost_tracker.record(make_cost_record(cost_usd=100.0, input_tokens=100000))

        summary = await cost_tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )
        assert summary.alert_level == BudgetAlertLevel.HARD_STOP

    @pytest.mark.unit
    async def test_alert_no_budget_config(self) -> None:
        tracker = CostTracker()
        await tracker.record(make_cost_record(cost_usd=999.0, input_tokens=999000))

        summary = await tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )
        assert summary.alert_level == BudgetAlertLevel.NORMAL
        assert summary.budget_used_percent == 0.0

    @pytest.mark.unit
    async def test_alert_zero_monthly(self) -> None:
        config = BudgetConfig(
            total_monthly=0.0,
            alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
            per_task_limit=0.0,
            per_agent_daily_limit=0.0,
        )
        tracker = CostTracker(budget_config=config)
        await tracker.record(make_cost_record(cost_usd=50.0, input_tokens=50000))

        summary = await tracker.build_summary(
            start=self._PERIOD_START, end=self._PERIOD_END
        )
        assert summary.alert_level == BudgetAlertLevel.NORMAL
        assert summary.budget_used_percent == 0.0
