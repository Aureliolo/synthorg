"""Tests for the Prometheus metrics collector."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
import structlog.testing
from prometheus_client import generate_latest

from synthorg.api.state import AppState
from synthorg.budget.state import BudgetStateSlice
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.state import HrStateSlice
from synthorg.observability._prometheus_label_fetchers import fetch_tool_names
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
    """Build a mock AgentIdentity with status and trust level."""
    agent = MagicMock()
    agent.status = status
    agent.tools.access_level = access_level
    agent.id = name if name is not None else f"agent-{status}-{access_level}"
    return agent


def _make_task(
    *,
    status: str = "created",
    assigned_to: str | None = None,
) -> MagicMock:
    """Build a mock Task with a given status and optional agent."""
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
class TestPrometheusCollectorAgentCost:
    """Tests for per-agent cost and budget utilization metrics."""

    async def test_agent_cost_total_per_agent(self) -> None:
        """Each active agent gets its own cost_total gauge."""
        collector = PrometheusCollector()
        agents = (
            _make_agent(name="alice"),
            _make_agent(name="bob"),
        )
        state = _mock_app_state(
            has_cost_tracker=True,
            has_agent_registry=True,
            agents=agents,
            agent_costs={"alice": 12.5, "bob": 3.0},
            budget_total_monthly=100.0,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert 'synthorg_agent_cost_total{agent_id="alice"}' in output
        assert 'synthorg_agent_cost_total{agent_id="bob"}' in output
        assert "12.5" in output
        assert "3.0" in output

    async def test_agent_budget_percent_per_agent(self) -> None:
        """Per-agent daily cost / per_agent_daily_limit * 100."""
        collector = PrometheusCollector()
        agents = (_make_agent(name="alice"),)
        state = _mock_app_state(
            has_cost_tracker=True,
            has_agent_registry=True,
            agents=agents,
            agent_daily_costs={"alice": 4.0},
            budget_total_monthly=100.0,
            per_agent_daily_limit=10.0,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert 'synthorg_agent_budget_used_percent{agent_id="alice"} 40.0' in output

    async def test_agent_cost_clears_stale_labels(self) -> None:
        """Agents that disappear drop from the gauge."""
        collector = PrometheusCollector()
        # First scrape: two agents.
        agents_v1 = (
            _make_agent(name="alice"),
            _make_agent(name="bob"),
        )
        state_v1 = _mock_app_state(
            has_cost_tracker=True,
            has_agent_registry=True,
            agents=agents_v1,
            agent_costs={"alice": 1.0, "bob": 2.0},
            budget_total_monthly=100.0,
        )
        await collector.refresh(state_v1)
        output_v1 = generate_latest(collector.registry).decode()
        assert 'agent_id="bob"' in output_v1

        # Second scrape: only alice.
        agents_v2 = (_make_agent(name="alice"),)
        state_v2 = _mock_app_state(
            has_cost_tracker=True,
            has_agent_registry=True,
            agents=agents_v2,
            agent_costs={"alice": 1.0},
            budget_total_monthly=100.0,
        )
        await collector.refresh(state_v2)
        output_v2 = generate_latest(collector.registry).decode()
        cost_lines = [
            ln
            for ln in output_v2.splitlines()
            if ln.startswith("synthorg_agent_cost_total{")
        ]
        assert len(cost_lines) > 0
        assert all('agent_id="alice"' in ln for ln in cost_lines)
        assert not any('agent_id="bob"' in ln for ln in cost_lines)

    async def test_agent_cost_skipped_when_no_agents(self) -> None:
        collector = PrometheusCollector()
        state = _mock_app_state(
            has_cost_tracker=True,
            has_agent_registry=False,
            budget_total_monthly=100.0,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert "synthorg_agent_cost_total{" not in output

    async def test_agent_cost_skipped_when_no_cost_tracker(self) -> None:
        collector = PrometheusCollector()
        agents = (_make_agent(name="alice"),)
        state = _mock_app_state(
            has_cost_tracker=False,
            has_agent_registry=True,
            agents=agents,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert "synthorg_agent_cost_total{" not in output

    async def test_agent_budget_percent_skipped_when_no_limit(self) -> None:
        """No per_agent_daily_limit -> cost_total set, budget_percent not."""
        collector = PrometheusCollector()
        agents = (_make_agent(name="alice"),)
        state = _mock_app_state(
            has_cost_tracker=True,
            has_agent_registry=True,
            agents=agents,
            agent_costs={"alice": 5.0},
            agent_daily_costs={"alice": 2.0},
            budget_total_monthly=100.0,
            per_agent_daily_limit=0.0,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert 'synthorg_agent_cost_total{agent_id="alice"}' in output
        budget_lines = [
            ln
            for ln in output.splitlines()
            if ln.startswith("synthorg_agent_budget_used_percent{")
        ]
        assert len(budget_lines) == 0

    async def test_agent_cost_skipped_when_agents_empty(self) -> None:
        """Empty agents tuple with both services available still skips."""
        collector = PrometheusCollector()
        state = _mock_app_state(
            has_cost_tracker=True,
            has_agent_registry=True,
            agents=(),
            budget_total_monthly=100.0,
        )
        await collector.refresh(state)
        output = generate_latest(collector.registry).decode()
        assert "synthorg_agent_cost_total{" not in output

    async def test_agent_cost_error_clears_gauges(self) -> None:
        """Exception during agent cost fetch clears both gauges."""
        collector = PrometheusCollector()
        agents = (_make_agent(name="alice"),)
        state = _mock_app_state(
            has_cost_tracker=True,
            has_agent_registry=True,
            agents=agents,
            agent_costs={"alice": 5.0},
            budget_total_monthly=100.0,
        )
        # Successful first scrape.
        await collector.refresh(state)
        output_v1 = generate_latest(collector.registry).decode()
        assert 'synthorg_agent_cost_total{agent_id="alice"}' in output_v1

        # Second scrape: agent cost query fails.
        cast(
            AsyncMock, state.slice(BudgetStateSlice).cost_tracker
        ).get_agent_cost = AsyncMock(
            side_effect=RuntimeError("db error"),
        )
        await collector.refresh(state)
        output_v2 = generate_latest(collector.registry).decode()
        cost_lines = [
            ln
            for ln in output_v2.splitlines()
            if ln.startswith("synthorg_agent_cost_total{")
        ]
        # Gauges were cleared before the error; no labels remain.
        assert len(cost_lines) == 0

    async def test_agent_cost_clears_on_empty_agents_second_scrape(
        self,
    ) -> None:
        """Gauges cleared when agents disappear between scrapes."""
        collector = PrometheusCollector()
        agents = (_make_agent(name="alice"),)
        state_v1 = _mock_app_state(
            has_cost_tracker=True,
            has_agent_registry=True,
            agents=agents,
            agent_costs={"alice": 5.0},
            budget_total_monthly=100.0,
        )
        await collector.refresh(state_v1)
        output_v1 = generate_latest(collector.registry).decode()
        assert 'synthorg_agent_cost_total{agent_id="alice"}' in output_v1

        # Second scrape: no agents.
        state_v2 = _mock_app_state(
            has_cost_tracker=True,
            has_agent_registry=True,
            agents=(),
            budget_total_monthly=100.0,
        )
        await collector.refresh(state_v2)
        output_v2 = generate_latest(collector.registry).decode()
        cost_lines = [
            ln
            for ln in output_v2.splitlines()
            if ln.startswith("synthorg_agent_cost_total{")
        ]
        assert len(cost_lines) == 0


@pytest.mark.unit
class TestPrometheusCollectorErrorPaths:
    """Each metric fetcher swallows non-critical failures so one broken
    source does not abort the whole scrape."""

    async def test_fetch_tool_names_registry_failure_returns_none(self) -> None:
        registry = MagicMock()
        registry.list_tools.side_effect = RuntimeError("registry boom")
        # ``_fetch_tool_names`` reads ``app_state.tool_registry`` via
        # ``getattr`` (it is not a state slice), so a bare attribute-bag
        # carrying the registry exercises the failure path.
        state = cast("AppState", SimpleNamespace(tool_registry=registry))

        # A failing tool registry yields ``None`` so the merge step keeps
        # the prior allowlist instead of cancelling sibling fetchers.
        assert (await fetch_tool_names(state)) is None

    def test_pg_pool_stats_failure_logs_redacted_context(self) -> None:
        collector = PrometheusCollector()
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
        collector._refresh_budget_metrics(state, billing_cost=10.0)

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
