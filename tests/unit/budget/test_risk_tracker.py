"""Tests for the RiskTracker service."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.risk_config import RiskBudgetConfig
from synthorg.budget.risk_tracker import RiskTracker
from tests.unit.budget.conftest import make_risk_record

#: Reference time for the window tests, which place records relative to it
#: and then read headroom at it. Fixed rather than read off the wall clock:
#: nothing here depends on the current moment, and a test that reads it
#: fails on whichever day the arithmetic happens to land badly.
_FIXED_NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


@pytest.mark.unit
class TestRiskTrackerRecord:
    """Tests for recording risk entries."""

    async def test_record_and_count(self) -> None:
        tracker = RiskTracker()
        assert await tracker.get_record_count() == 0
        await tracker.record(make_risk_record())
        assert await tracker.get_record_count() == 1

    async def test_multiple_records(self) -> None:
        tracker = RiskTracker()
        for i in range(5):
            await tracker.record(
                make_risk_record(agent_id=f"agent-{i}"),
            )
        assert await tracker.get_record_count() == 5


@pytest.mark.unit
class TestRiskTrackerQueries:
    """Tests for risk aggregation queries."""

    async def test_get_total_risk_empty(self) -> None:
        tracker = RiskTracker()
        assert await tracker.get_total_risk() == 0.0

    async def test_get_total_risk(self) -> None:
        tracker = RiskTracker()
        await tracker.record(make_risk_record(risk_units=0.5))
        await tracker.record(make_risk_record(risk_units=0.3))
        total = await tracker.get_total_risk()
        assert total == pytest.approx(0.8)

    async def test_get_agent_risk(self) -> None:
        tracker = RiskTracker()
        await tracker.record(
            make_risk_record(agent_id="a", risk_units=0.5),
        )
        await tracker.record(
            make_risk_record(agent_id="b", risk_units=0.3),
        )
        await tracker.record(
            make_risk_record(agent_id="a", risk_units=0.2),
        )
        assert await tracker.get_agent_risk("a") == pytest.approx(0.7)
        assert await tracker.get_agent_risk("b") == pytest.approx(0.3)
        assert await tracker.get_agent_risk("c") == pytest.approx(0.0)

    async def test_get_task_risk(self) -> None:
        tracker = RiskTracker()
        await tracker.record(
            make_risk_record(task_id="t1", risk_units=0.4),
        )
        await tracker.record(
            make_risk_record(task_id="t2", risk_units=0.6),
        )
        assert await tracker.get_task_risk("t1") == pytest.approx(0.4)
        assert await tracker.get_task_risk("t2") == pytest.approx(0.6)

    async def test_time_range_filter(self) -> None:
        tracker = RiskTracker()
        now = datetime.now(UTC)
        old = now - timedelta(hours=2)
        await tracker.record(make_risk_record(risk_units=0.5, timestamp=old))
        await tracker.record(make_risk_record(risk_units=0.3, timestamp=now))
        total = await tracker.get_total_risk(
            start=now - timedelta(hours=1),
        )
        assert total == pytest.approx(0.3)

    async def test_get_records_all(self) -> None:
        tracker = RiskTracker()
        await tracker.record(make_risk_record(agent_id="a"))
        await tracker.record(make_risk_record(agent_id="b"))
        records = await tracker.get_records()
        assert len(records) == 2

    async def test_get_records_filtered_by_agent(self) -> None:
        tracker = RiskTracker()
        await tracker.record(make_risk_record(agent_id="a"))
        await tracker.record(make_risk_record(agent_id="b"))
        records = await tracker.get_records(agent_id="a")
        assert len(records) == 1
        assert records[0].agent_id == "a"

    async def test_get_records_filtered_by_task(self) -> None:
        tracker = RiskTracker()
        await tracker.record(make_risk_record(task_id="t1"))
        await tracker.record(make_risk_record(task_id="t2"))
        records = await tracker.get_records(task_id="t1")
        assert len(records) == 1
        assert records[0].task_id == "t1"

    async def test_get_records_filtered_by_action_type(self) -> None:
        tracker = RiskTracker()
        await tracker.record(
            make_risk_record(action_type="code:write"),
        )
        await tracker.record(
            make_risk_record(action_type="code:read"),
        )
        records = await tracker.get_records(action_type="code:write")
        assert len(records) == 1

    async def test_get_records_returns_immutable_tuple(self) -> None:
        tracker = RiskTracker()
        await tracker.record(make_risk_record())
        records = await tracker.get_records()
        assert isinstance(records, tuple)

    async def test_invalid_time_range_rejected(self) -> None:
        tracker = RiskTracker()
        now = datetime.now(UTC)
        with pytest.raises(ValueError, match=r"start.*end"):
            await tracker.get_total_risk(
                start=now,
                end=now - timedelta(hours=1),
            )


@pytest.mark.unit
class TestRiskTrackerPruning:
    """Tests for TTL-based eviction."""

    async def test_prune_expired(self) -> None:
        tracker = RiskTracker()
        now = _FIXED_NOW
        old = now - timedelta(hours=200)
        await tracker.record(make_risk_record(timestamp=old))
        await tracker.record(make_risk_record(timestamp=now))
        pruned = await tracker.prune_expired(now=now)
        assert pruned == 1
        assert await tracker.get_record_count() == 1

    async def test_prune_nothing_when_all_fresh(self) -> None:
        tracker = RiskTracker()
        await tracker.record(make_risk_record())
        pruned = await tracker.prune_expired()
        assert pruned == 0
        assert await tracker.get_record_count() == 1

    async def test_auto_prune_on_threshold(self) -> None:
        tracker = RiskTracker(auto_prune_threshold=5)
        now = datetime.now(UTC)
        old = now - timedelta(hours=200)
        # Add 3 old + 3 fresh to exceed threshold of 5
        for _ in range(3):
            await tracker.record(make_risk_record(timestamp=old))
        for _ in range(3):
            await tracker.record(make_risk_record(timestamp=now))
        # Snapshot triggers auto-prune
        _ = await tracker.get_total_risk()
        assert await tracker.get_record_count() == 3

    async def test_auto_prune_threshold_validation(self) -> None:
        with pytest.raises(ValueError, match="auto_prune_threshold"):
            RiskTracker(auto_prune_threshold=0)


@pytest.mark.unit
class TestRiskTrackerConfig:
    """Tests for config-aware construction."""

    def test_construction_without_config(self) -> None:
        tracker = RiskTracker()
        assert tracker.risk_budget_config is None

    def test_construction_with_config(self) -> None:
        cfg = RiskBudgetConfig(enabled=True)
        tracker = RiskTracker(risk_budget_config=cfg)
        assert tracker.risk_budget_config is cfg


def _daily_limit(total: float) -> RiskBudgetConfig:
    """A risk budget whose only interesting dial is its daily total.

    The per-task and per-agent limits are pulled down with it because the
    config refuses a hierarchy where a narrower limit exceeds a wider one.

    Returns:
        A ``RiskBudgetConfig`` with ``total_daily_risk_limit`` set to *total*.
    """
    return RiskBudgetConfig(
        enabled=True,
        per_task_risk_limit=total,
        per_agent_daily_risk_limit=total,
        total_daily_risk_limit=total,
    )


@pytest.mark.unit
class TestRiskTrackerHeadroom:
    """The synchronous signal ``BUDGET_AWARE`` autonomy promotion reads."""

    async def test_an_untouched_daily_budget_is_full_headroom(self) -> None:
        tracker = RiskTracker(risk_budget_config=_daily_limit(10.0))

        assert tracker.headroom_fraction() == pytest.approx(1.0)

    async def test_headroom_is_the_unspent_share_of_the_daily_limit(self) -> None:
        tracker = RiskTracker(risk_budget_config=_daily_limit(10.0))
        await tracker.record(make_risk_record(risk_units=2.5))

        assert tracker.headroom_fraction() == pytest.approx(0.75)

    async def test_an_exhausted_budget_reports_zero_never_negative(self) -> None:
        tracker = RiskTracker(risk_budget_config=_daily_limit(1.0))
        await tracker.record(make_risk_record(risk_units=5.0))

        assert tracker.headroom_fraction() == 0.0

    async def test_only_the_trailing_day_counts_against_a_daily_limit(self) -> None:
        now = _FIXED_NOW
        tracker = RiskTracker(risk_budget_config=_daily_limit(10.0))
        # Inside the 168-hour retention window but outside today's budget.
        await tracker.record(
            make_risk_record(risk_units=9.0, timestamp=now - timedelta(hours=30)),
        )
        await tracker.record(
            make_risk_record(risk_units=1.0, timestamp=now - timedelta(hours=1)),
        )

        assert tracker.headroom_fraction(now=now) == pytest.approx(0.9)

    def test_no_configured_limit_is_full_headroom_not_a_freeze(self) -> None:
        unconfigured = RiskTracker()
        unbounded = RiskTracker(risk_budget_config=_daily_limit(0.0))

        assert unconfigured.headroom_fraction() == pytest.approx(1.0)
        assert unbounded.headroom_fraction() == pytest.approx(1.0)

    async def test_reading_headroom_prunes_the_ledger_it_scans(self) -> None:
        """The promotion path reaches no other auto-prune.

        ``_snapshot`` prunes, but only the async query methods go through it
        and the promotion decision calls none of them. Without pruning here
        the ledger grows for the life of the process and every decision
        scans all of it.
        """
        tracker = RiskTracker(
            risk_budget_config=_daily_limit(10.0),
            auto_prune_threshold=3,
        )
        now = _FIXED_NOW
        for _ in range(4):
            await tracker.record(
                make_risk_record(risk_units=0.1, timestamp=now - timedelta(hours=200)),
            )
        await tracker.record(
            make_risk_record(risk_units=1.0, timestamp=now - timedelta(hours=1)),
        )

        assert tracker.headroom_fraction(now=now) == pytest.approx(0.9)
        assert await tracker.get_record_count() == 1
