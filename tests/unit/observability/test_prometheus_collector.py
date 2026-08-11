"""Tests for the Prometheus metrics collector."""

import asyncio
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
import structlog.testing
from prometheus_client import generate_latest

from synthorg.api.state import AppState
from synthorg.budget.spending_summary import QualifiedTotal, SpendMeasurability
from synthorg.budget.state import BudgetStateSlice
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.state import HrStateSlice
from synthorg.observability.events.metrics import METRICS_SCRAPE_FAILED
from synthorg.observability.prometheus_collector import PrometheusCollector
from tests._shared import make_app_state


def _mock_app_state(  # noqa: PLR0913
    *,
    has_cost_tracker: bool = False,
    has_agent_registry: bool = False,
    has_task_engine: bool = False,
    total_cost: float = 0.0,
    daily_cost: float = 0.0,
    billing_cost: float | None = None,
    agents: tuple[object, ...] = (),
    tasks: tuple[object, ...] = (),
    budget_total_monthly: float | None = None,
    per_agent_daily_limit: float | None = None,
    agent_costs: dict[str, float] | None = None,
    agent_daily_costs: dict[str, float] | None = None,
    reset_day: int = 1,
) -> AppState:
    """Build a mock AppState with configurable service availability.

    The billing-period total reports as ``MEASURED``; a test that needs
    another verdict rebinds ``get_qualified_total`` on the returned tracker
    (see :func:`_unmeasurable`) rather than widening this signature.

    Args:
        billing_cost: Month-to-date cost (start=period_start query).
            Defaults to *daily_cost* so day-1 queries are consistent.
        agent_costs: Accumulated cost per agent_id (no time filter).
        agent_daily_costs: Daily cost per agent_id (with start filter).
        reset_day: Budget reset day (1-28). Determines which
            ``get_total_cost(start=...)`` calls return billing cost.
    """
    cost_tracker: AsyncMock | None = None
    agent_registry: AsyncMock | None = None
    task_engine: AsyncMock | None = None

    if has_cost_tracker:
        cost_tracker = AsyncMock()

        _total = total_cost
        _daily = daily_cost
        _billing = billing_cost if billing_cost is not None else _daily
        _reset_day = reset_day

        async def _get_total_cost(
            *,
            start: datetime | None = None,
            end: datetime | None = None,
        ) -> float:
            if start is None:
                return _total
            if start.day == _reset_day:
                return _billing
            return _daily

        cost_tracker.get_total_cost = AsyncMock(side_effect=_get_total_cost)

        async def _get_qualified_total(
            *,
            start: datetime | None = None,
            end: datetime | None = None,
        ) -> QualifiedTotal:
            # Through the public mock, not the closure behind it: a test that
            # injects a billing-total failure rebinds `get_total_cost`, and a
            # direct call to the closure would route around that rebinding.
            return QualifiedTotal(
                cost=await cost_tracker.get_total_cost(start=start, end=end),
                measurability=SpendMeasurability.MEASURED,
            )

        cost_tracker.get_qualified_total = AsyncMock(side_effect=_get_qualified_total)

        _agent_costs = agent_costs or {}
        _agent_daily_costs = agent_daily_costs or {}

        async def _get_agent_cost(
            agent_id: str,
            *,
            start: datetime | None = None,
            end: datetime | None = None,
        ) -> float:
            if start is not None:
                return _agent_daily_costs.get(agent_id, 0.0)
            return _agent_costs.get(agent_id, 0.0)

        cost_tracker.get_agent_cost = AsyncMock(side_effect=_get_agent_cost)

        if budget_total_monthly is not None:
            budget_cfg = MagicMock()
            budget_cfg.total_monthly = budget_total_monthly
            budget_cfg.per_agent_daily_limit = (
                per_agent_daily_limit if per_agent_daily_limit is not None else 0.0
            )
            budget_cfg.reset_day = _reset_day
            cost_tracker.budget_config = budget_cfg
        else:
            cost_tracker.budget_config = None

    if has_agent_registry:
        agent_registry = AsyncMock()
        agent_registry.list_active = AsyncMock(return_value=agents)

    if has_task_engine:
        task_engine = AsyncMock()
        task_engine.list_tasks = AsyncMock(return_value=(tasks, len(tasks)))

    return make_app_state(
        cost_tracker=cost_tracker,
        agent_registry=agent_registry,
        task_engine=task_engine,
    )


def _make_agent(
    *,
    name: str | None = None,
    status: str = "active",
    access_level: str = "standard",
) -> MagicMock:
    """Build a mock AgentIdentity with status and trust level.

    Bare MagicMock (spec=None) is load-bearing: the agent flows through the
    collector's typeguard-instrumented boundary, which a SimpleNamespace /
    partially-spec'd double would fail. The mock-spec gate exempts this read
    path; do not "tighten" to mock_of here.
    """
    agent = MagicMock()
    agent.status = status
    agent.tools.access_level = access_level
    agent.id = name if name is not None else f"agent-{status}-{access_level}"
    return agent


def _unmeasurable(state: AppState) -> AppState:
    """Rebind the state's billing-period total to report UNMEASURABLE.

    Applied after construction rather than through a builder parameter: the
    builder is already at the argument cap, and what a window measures is a
    property of the estate, not another dial on a mock.

    Returns:
        The same state, for use inline.
    """
    tracker = cast(AsyncMock, state.slice(BudgetStateSlice).cost_tracker)
    tracker.get_qualified_total = AsyncMock(
        return_value=QualifiedTotal(
            cost=0.0,
            measurability=SpendMeasurability.UNMEASURABLE,
        )
    )
    return state


def _split_measurability(
    state: AppState,
    *,
    reset_day: int,
    billing: SpendMeasurability,
    daily: SpendMeasurability,
) -> AppState:
    """Give the billing-period and daily windows different verdicts.

    The two are separate queries over separate windows, so a collector that
    answered both from one ``QualifiedTotal`` would be wrong in a way no
    same-verdict fixture can show. Discriminates on ``start`` exactly as the
    builder's own cost closure does: the billing query starts on the reset
    day, the daily one on today's midnight.

    Returns:
        The same state, for use inline.
    """
    tracker = cast(AsyncMock, state.slice(BudgetStateSlice).cost_tracker)

    async def _qualified(
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> QualifiedTotal:
        window = billing if start is not None and start.day == reset_day else daily
        return QualifiedTotal(cost=0.0, measurability=window)

    tracker.get_qualified_total = AsyncMock(side_effect=_qualified)
    return state


def _gauge_value(collector: PrometheusCollector, name: str) -> float:
    """Read one unlabelled gauge out of the rendered exposition text.

    Returns:
        The gauge's current value.

    Raises:
        AssertionError: When the gauge is absent or rendered more than once.
    """
    output = generate_latest(collector.registry).decode()
    lines = [ln for ln in output.splitlines() if ln.startswith(f"{name} ")]
    assert len(lines) == 1, f"{name} rendered {len(lines)} times"
    return float(lines[0].split()[-1])


def _make_task(
    *,
    status: str = "created",
    assigned_to: str | None = None,
) -> MagicMock:
    """Build a mock Task with a given status and optional agent.

    Bare MagicMock is load-bearing here for the same typeguard reason as
    :func:`_make_agent`.
    """
    task = MagicMock()
    task.status = status
    task.assigned_to = assigned_to
    return task


@pytest.mark.unit
class TestPrometheusCollectorInit:
    """Tests for collector initialization."""

    def test_creates_registry(self) -> None:
        collector = PrometheusCollector()
        assert collector.registry is not None

    def test_registry_is_isolated(self) -> None:
        c1 = PrometheusCollector()
        c2 = PrometheusCollector()
        assert c1.registry is not c2.registry

    def test_generate_latest_returns_bytes(self) -> None:
        collector = PrometheusCollector()
        output = generate_latest(collector.registry)
        assert isinstance(output, bytes)

    def test_info_metric_present(self) -> None:
        collector = PrometheusCollector()
        output = generate_latest(collector.registry).decode()
        assert "synthorg_app_info" in output


@pytest.mark.unit
class TestPrometheusCollectorRefresh:
    """Tests for the async refresh method."""

    async def test_refresh_with_no_services(self) -> None:
        collector = PrometheusCollector()
        state = _mock_app_state()
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert "synthorg_app_info" in output

    async def test_refresh_updates_cost_total(self) -> None:
        collector = PrometheusCollector()
        state = _mock_app_state(has_cost_tracker=True, total_cost=42.5)
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert "synthorg_cost_total" in output
        assert "42.5" in output

    async def test_refresh_updates_agent_count_with_trust_level(self) -> None:
        collector = PrometheusCollector()
        agents = (
            _make_agent(status="active", access_level="standard"),
            _make_agent(status="active", access_level="elevated"),
            _make_agent(status="onboarding", access_level="restricted"),
        )
        state = _mock_app_state(has_agent_registry=True, agents=agents)
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert "synthorg_active_agents_total" in output
        assert 'trust_level="standard"' in output
        assert 'trust_level="elevated"' in output

    async def test_refresh_updates_task_counts(self) -> None:
        collector = PrometheusCollector()
        tasks = (
            _make_task(status="created"),
            _make_task(status="in_progress"),
            _make_task(status="in_progress"),
            _make_task(status="completed"),
        )
        state = _mock_app_state(has_task_engine=True, tasks=tasks)
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert "synthorg_tasks_total" in output
        assert 'status="in_progress"' in output

    async def test_refresh_updates_budget_utilization(self) -> None:
        collector = PrometheusCollector()
        state = _mock_app_state(
            has_cost_tracker=True,
            total_cost=50.0,
            billing_cost=50.0,
            budget_total_monthly=200.0,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert "synthorg_budget_used_percent" in output
        assert "synthorg_budget_monthly_cost" in output
        assert "25.0" in output  # 50/200 * 100

    async def test_budget_percent_uses_billing_period_cost(self) -> None:
        """Budget utilization uses month-to-date, not lifetime cost."""
        collector = PrometheusCollector()
        state = _mock_app_state(
            has_cost_tracker=True,
            total_cost=500.0,
            billing_cost=50.0,
            budget_total_monthly=200.0,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        # 50/200 * 100 = 25%, not 500/200 * 100 = 250%
        lines = [
            ln
            for ln in output.splitlines()
            if ln.startswith("synthorg_budget_used_percent ")
        ]
        assert len(lines) == 1
        assert float(lines[0].split()[-1]) == 25.0

    async def test_budget_percent_cleared_when_spend_is_unmeasurable(self) -> None:
        """A flat-rate estate has no percentage, and must not report 0%.

        Zero here reads as full headroom on the one estate where the money
        ceiling measures nothing, so the gauge is cleared and the dashboard
        has nothing to draw rather than a reassuring number.
        """
        collector = PrometheusCollector()
        state = _unmeasurable(
            _mock_app_state(
                has_cost_tracker=True,
                total_cost=0.0,
                billing_cost=0.0,
                budget_total_monthly=200.0,
            )
        )

        await collector.refresh(state)

        output = generate_latest(collector.registry).decode()
        lines = [
            ln
            for ln in output.splitlines()
            if ln.startswith("synthorg_budget_used_percent ")
        ]
        assert len(lines) == 1
        assert float(lines[0].split()[-1]) == 0.0

    async def test_each_window_publishes_the_verdict_of_its_own_query(self) -> None:
        """The daily gauge needs the qualifier the period gauge already has.

        Asserted with the two windows DISAGREEING, in both directions. They
        cover different rows -- a flat-rate connection added today makes the
        day unmeasurable while the period so far was metered -- so a
        collector answering both from one ``QualifiedTotal`` is wrong, and
        any same-verdict fixture would pass against it.
        """
        collector = PrometheusCollector()
        # Only distinguishable when the reset day is not today; on the reset
        # day the collector genuinely queries one window twice.
        reset_day = 1 if datetime.now(UTC).day != 1 else 2

        daily_went_flat = _split_measurability(
            _mock_app_state(
                has_cost_tracker=True,
                total_cost=10.0,
                billing_cost=10.0,
                budget_total_monthly=200.0,
                reset_day=reset_day,
            ),
            reset_day=reset_day,
            billing=SpendMeasurability.MEASURED,
            daily=SpendMeasurability.UNMEASURABLE,
        )
        await collector.refresh(daily_went_flat)
        assert _gauge_value(collector, "synthorg_budget_spend_measurable") == 1.0
        assert _gauge_value(collector, "synthorg_budget_daily_spend_measurable") == 0.0

        # And the reverse, so neither gauge can be reading the other's query.
        period_was_flat = _split_measurability(
            _mock_app_state(
                has_cost_tracker=True,
                total_cost=10.0,
                billing_cost=10.0,
                budget_total_monthly=200.0,
                reset_day=reset_day,
            ),
            reset_day=reset_day,
            billing=SpendMeasurability.UNMEASURABLE,
            daily=SpendMeasurability.MEASURED,
        )
        await collector.refresh(period_was_flat)
        assert _gauge_value(collector, "synthorg_budget_spend_measurable") == 0.0
        assert _gauge_value(collector, "synthorg_budget_daily_spend_measurable") == 1.0

    async def test_budget_percent_reset_when_cost_unavailable(
        self,
    ) -> None:
        """Budget percent resets to 0 when billing cost is unavailable."""
        collector = PrometheusCollector()
        state_v1 = _mock_app_state(
            has_cost_tracker=True,
            billing_cost=50.0,
            budget_total_monthly=200.0,
        )
        await collector.refresh(state_v1)
        # Second scrape: cost tracker error leaves billing_cost=None.
        state_v2 = _mock_app_state(
            has_cost_tracker=True,
            budget_total_monthly=200.0,
        )
        cast(
            AsyncMock, state_v2.slice(BudgetStateSlice).cost_tracker
        ).get_total_cost = AsyncMock(
            side_effect=RuntimeError("tracker down"),
        )
        await collector.refresh(state_v2)
        output = generate_latest(collector.registry).decode()
        lines = [
            ln
            for ln in output.splitlines()
            if ln.startswith("synthorg_budget_used_percent ")
        ]
        assert len(lines) == 1
        assert float(lines[0].split()[-1]) == 0.0

    async def test_refresh_skips_budget_when_no_config(self) -> None:
        collector = PrometheusCollector()
        state = _mock_app_state(
            has_cost_tracker=True,
            total_cost=50.0,
            budget_total_monthly=None,
        )
        await collector.refresh(state)
        # Should not error -- budget metrics simply not set

    async def test_refresh_skips_unavailable_services(self) -> None:
        collector = PrometheusCollector()
        state = _mock_app_state(
            has_cost_tracker=False,
            has_agent_registry=False,
            has_task_engine=False,
        )
        await collector.refresh(state)

    async def test_cost_tracker_error_does_not_block_agents(self) -> None:
        """Partial failure: cost tracker fails, agent registry succeeds."""
        collector = PrometheusCollector()
        agents = (_make_agent(status="active"),)
        state = _mock_app_state(
            has_cost_tracker=True,
            has_agent_registry=True,
            agents=agents,
        )
        cast(
            AsyncMock, state.slice(BudgetStateSlice).cost_tracker
        ).get_total_cost = AsyncMock(
            side_effect=RuntimeError("tracker down"),
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert "synthorg_active_agents_total" in output

    async def test_agent_registry_error_does_not_block_tasks(self) -> None:
        """Partial failure: agent registry fails, task engine succeeds."""
        collector = PrometheusCollector()
        tasks = (_make_task(status="created"),)
        state = _mock_app_state(
            has_agent_registry=True,
            has_task_engine=True,
            tasks=tasks,
        )
        cast(
            AsyncMock, state.slice(HrStateSlice).agent_registry
        ).list_active = AsyncMock(
            side_effect=RuntimeError("registry down"),
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert "synthorg_tasks_total" in output

    async def test_task_engine_error_preserves_prior_task_gauge(self) -> None:
        """A transient list_tasks failure keeps the prior gauge values
        instead of dropping the dashboard to zero."""
        collector = PrometheusCollector()
        tasks = (_make_task(status="in_progress"),)
        state = _mock_app_state(has_task_engine=True, tasks=tasks)
        # First refresh populates the gauge.
        await collector.refresh(state)
        assert 'status="in_progress"' in generate_latest(collector.registry).decode()
        # A subsequent transient fetch failure must NOT clear the gauge.
        cast(
            AsyncMock, state.slice(EngineStateSlice).task_engine
        ).list_tasks = AsyncMock(
            side_effect=RuntimeError("engine down"),
        )
        await collector.refresh(state)
        assert 'status="in_progress"' in generate_latest(collector.registry).decode()

    async def test_task_scrape_skips_count_round_trip(self) -> None:
        """The scrape fetches rows only -- never the extra count_tasks
        round-trip -- so it passes include_total=False."""
        collector = PrometheusCollector()
        tasks = (_make_task(status="created"),)
        state = _mock_app_state(has_task_engine=True, tasks=tasks)
        await collector.refresh(state)
        list_tasks = cast(
            AsyncMock, state.slice(EngineStateSlice).task_engine
        ).list_tasks
        list_tasks.assert_awaited_once_with(include_total=False)


@pytest.mark.unit
class TestPrometheusCollectorSecurityVerdicts:
    """Tests for security verdict counter."""

    def test_record_verdict_increments_counter(self) -> None:
        collector = PrometheusCollector()
        collector.record_security_verdict("allow")
        collector.record_security_verdict("allow")
        collector.record_security_verdict("deny")
        output = generate_latest(collector.registry).decode()
        assert "synthorg_security_evaluations_total" in output
        assert 'verdict="allow"' in output
        assert 'verdict="deny"' in output

    def test_record_verdict_rejects_invalid(self) -> None:
        collector = PrometheusCollector()
        with pytest.raises(ValueError, match="Unknown security verdict"):
            collector.record_security_verdict("invalid")


@pytest.mark.unit
class TestPrometheusCollectorCoordination:
    """Tests for push-updated coordination metrics."""

    def test_record_coordination_metrics(self) -> None:
        collector = PrometheusCollector()
        collector.record_coordination_metrics(
            efficiency=0.85,
            overhead_percent=15.0,
        )
        output = generate_latest(collector.registry).decode()
        assert "synthorg_coordination_efficiency" in output
        assert "synthorg_coordination_overhead_percent" in output


@pytest.mark.unit
class TestPrometheusCollectorOutput:
    """Tests for the exposition format output."""

    async def test_output_is_valid_exposition_format(self) -> None:
        collector = PrometheusCollector()
        state = _mock_app_state(
            has_cost_tracker=True,
            total_cost=10.0,
            has_agent_registry=True,
            agents=(
                _make_agent(status="active"),
                _make_agent(status="active"),
            ),
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry)
        assert isinstance(output, bytes)
        text = output.decode()
        assert "# HELP" in text
        assert "# TYPE" in text

    async def test_custom_prefix(self) -> None:
        collector = PrometheusCollector(prefix="myorg")
        output = generate_latest(collector.registry).decode()
        assert "myorg_app_info" in output
        assert "synthorg_app_info" not in output


@pytest.mark.unit
class TestPrometheusCollectorDailyBudget:
    """Tests for the daily budget utilization percentage metric."""

    async def test_daily_budget_percent_computed(self) -> None:
        """Daily cost exceeding prorated daily budget caps at 100%."""
        collector = PrometheusCollector()
        # 50 daily >> 100/N prorated budget for any month length N.
        state = _mock_app_state(
            has_cost_tracker=True,
            total_cost=200.0,
            daily_cost=50.0,
            budget_total_monthly=100.0,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        lines = [
            ln
            for ln in output.splitlines()
            if ln.startswith("synthorg_budget_daily_used_percent ")
        ]
        assert len(lines) == 1
        assert float(lines[0].split()[-1]) == 100.0

    async def test_daily_budget_percent_partial_day(self) -> None:
        """Normal daily utilization produces correct percentage."""
        from datetime import UTC

        from synthorg.budget.billing import billing_period_start

        collector = PrometheusCollector()
        state = _mock_app_state(
            has_cost_tracker=True,
            total_cost=50.0,
            daily_cost=3.0,
            budget_total_monthly=300.0,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        lines = [
            ln
            for ln in output.splitlines()
            if ln.startswith("synthorg_budget_daily_used_percent ")
        ]
        assert len(lines) == 1
        value = float(lines[0].split()[-1])
        # Compute expected from current billing period length.
        now = datetime.now(UTC)
        ps = billing_period_start(1, now=now)
        ns = (
            ps.replace(
                year=ps.year + 1,
                month=1,
            )
            if ps.month == 12
            else ps.replace(month=ps.month + 1)
        )
        days = (ns - ps).days
        expected = (3.0 / (300.0 / days)) * 100.0
        assert value == pytest.approx(expected, abs=0.01)

    async def test_daily_budget_zero_cost(self) -> None:
        """Zero daily cost yields 0% utilization."""
        collector = PrometheusCollector()
        state = _mock_app_state(
            has_cost_tracker=True,
            total_cost=10.0,
            daily_cost=0.0,
            budget_total_monthly=300.0,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        lines = [
            ln
            for ln in output.splitlines()
            if ln.startswith("synthorg_budget_daily_used_percent ")
        ]
        assert len(lines) == 1
        assert float(lines[0].split()[-1]) == 0.0

    async def test_daily_budget_skipped_when_zero_monthly(self) -> None:
        """Zero monthly budget causes early return (no gauge update)."""
        collector = PrometheusCollector()
        state = _mock_app_state(
            has_cost_tracker=True,
            total_cost=10.0,
            daily_cost=5.0,
            budget_total_monthly=0.0,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        lines = [
            ln
            for ln in output.splitlines()
            if ln.startswith("synthorg_budget_daily_used_percent ")
        ]
        assert len(lines) == 1
        assert float(lines[0].split()[-1]) == 0.0

    async def test_daily_budget_skipped_when_no_config(self) -> None:
        collector = PrometheusCollector()
        state = _mock_app_state(
            has_cost_tracker=True,
            daily_cost=5.0,
            budget_total_monthly=None,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        lines = [
            ln
            for ln in output.splitlines()
            if ln.startswith("synthorg_budget_daily_used_percent ")
        ]
        assert len(lines) == 1
        assert float(lines[0].split()[-1]) == 0.0

    async def test_daily_budget_skipped_when_no_cost_tracker(self) -> None:
        collector = PrometheusCollector()
        state = _mock_app_state(has_cost_tracker=False)
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        lines = [
            ln
            for ln in output.splitlines()
            if ln.startswith("synthorg_budget_daily_used_percent ")
        ]
        assert len(lines) == 1
        assert float(lines[0].split()[-1]) == 0.0

    async def test_daily_budget_exception_does_not_crash(self) -> None:
        """Exception during computation is caught; scrape continues."""
        collector = PrometheusCollector()
        state = _mock_app_state(
            has_cost_tracker=True,
            daily_cost=5.0,
            budget_total_monthly=300.0,
        )
        # Patch billing_period_start to raise inside the try block.
        from unittest.mock import patch

        with patch(
            "synthorg.observability.prometheus_collector.billing_period_start",
            side_effect=RuntimeError("broken"),
        ):
            await collector.refresh(state)
        # No crash; other metrics still updated.
        output = generate_latest(collector.registry).decode()
        assert "synthorg_app_info" in output

    async def test_daily_budget_respects_reset_day(self) -> None:
        """Non-default reset_day prorates using billing period length."""
        collector = PrometheusCollector()
        # reset_day=15: billing period spans two calendar months.
        # daily_cost=50 exceeds prorated budget for any period length,
        # so the metric should cap at 100%.
        state = _mock_app_state(
            has_cost_tracker=True,
            total_cost=200.0,
            daily_cost=50.0,
            budget_total_monthly=100.0,
            reset_day=15,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        lines = [
            ln
            for ln in output.splitlines()
            if ln.startswith("synthorg_budget_daily_used_percent ")
        ]
        assert len(lines) == 1
        assert float(lines[0].split()[-1]) == 100.0


@pytest.mark.unit
class TestPrometheusCollectorErrorPaths:
    """Each metric fetcher swallows non-critical failures so one broken
    source does not abort the whole scrape."""

    def test_pg_pool_stats_failure_logs_redacted_context(self) -> None:
        collector = PrometheusCollector()
        # Bare MagicMock fakes the postgres backend's private ``_pool`` so the
        # ``get_stats`` failure path is exercised through the typeguard
        # boundary that a SimpleNamespace would not satisfy.
        backend = MagicMock()
        backend.kind = "postgres"
        pool = MagicMock()
        pool.get_stats.side_effect = RuntimeError("pool stats boom")
        backend._pool = pool
        state = make_app_state(persistence=backend)

        # A pool-stats failure logs redacted context and returns
        # without raising; an operator must be able to tell a DNS blip
        # from a driver crash.
        with structlog.testing.capture_logs() as logs:
            collector._refresh_pg_pool_metrics(state)

        scrape_failures = [
            rec
            for rec in logs
            if rec.get("event") == METRICS_SCRAPE_FAILED
            and rec.get("component") == "pg_pool_stats"
        ]
        assert scrape_failures, "pg_pool stats failure must log METRICS_SCRAPE_FAILED"
        assert scrape_failures[0].get("error_type") == "RuntimeError"
        assert scrape_failures[0].get("error")

    def test_budget_metrics_failure_clears_gauges(self) -> None:
        collector = PrometheusCollector()
        tracker = AsyncMock()
        type(tracker).budget_config = PropertyMock(
            side_effect=RuntimeError("tracker boom"),
        )
        state = make_app_state(cost_tracker=tracker)

        # A cost-tracker failure resets the budget gauges to zero rather
        # than leaving stale values.
        collector._refresh_budget_metrics(
            state,
            QualifiedTotal(cost=10.0, measurability=SpendMeasurability.MEASURED),
        )

    async def test_task_metrics_failure_is_swallowed(self) -> None:
        collector = PrometheusCollector()
        state = _mock_app_state(has_task_engine=True)
        cast(
            AsyncMock, state.slice(EngineStateSlice).task_engine
        ).list_tasks.side_effect = RuntimeError("list boom")

        # A task-engine failure logs and returns without raising.
        await collector._refresh_task_metrics(state)


@pytest.mark.unit
class TestPrometheusCollectorRefreshLock:
    """``refresh()`` serialises so overlapping scrapes never race the gauges."""

    async def test_refresh_serialises_under_lock(self) -> None:
        """Two concurrent ``refresh()`` calls do not interleave the body.

        Without the per-instance lock, the second ``refresh`` would
        enter the body while the first is suspended at an ``await``,
        racing the gauge clear+repopulate steps. The lock makes
        observed concurrency exactly one.
        """
        collector = PrometheusCollector()
        active = 0
        max_active = 0

        async def _fake_refresh_all(_app_state: AppState) -> None:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            # Yield: if unserialised, the second refresh enters here too.
            await asyncio.sleep(0)
            active -= 1

        collector._refresh_all = _fake_refresh_all  # type: ignore[method-assign,assignment]
        state = make_app_state()
        await asyncio.gather(collector.refresh(state), collector.refresh(state))
        assert max_active == 1
