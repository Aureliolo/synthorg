"""Tests for CoordinationMetricsCollector runtime collection pipeline."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from synthorg.budget.baseline_store import BaselineRecord, BaselineStore
from synthorg.budget.coordination_collector import (
    CollectionInputs,
    CoordinationMetricsCollector,
    SimilarityComputer,
)
from synthorg.budget.coordination_config import (
    CoordinationMetricName,
    CoordinationMetricsConfig,
    OrchestrationAlertThresholds,
)
from synthorg.budget.coordination_metrics import CoordinationMetrics
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.communication.bus_protocol import MessageBus
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from synthorg.providers.enums import FinishReason
from tests._shared import FakeClock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(
    *,
    enabled: bool = True,
    collect: tuple[CoordinationMetricName, ...] | None = None,
    info: float = 0.10,
    warn: float = 0.50,
    critical: float = 0.70,
) -> CoordinationMetricsConfig:
    thresholds = OrchestrationAlertThresholds(info=info, warn=warn, critical=critical)
    return CoordinationMetricsConfig(
        enabled=enabled,
        collect=collect if collect is not None else tuple(CoordinationMetricName),
        orchestration_alerts=thresholds,
    )


def _turn(
    finish_reason: FinishReason = FinishReason.STOP,
    input_tokens: int = 100,
    output_tokens: int = 50,
    latency_ms: float | None = 50.0,
) -> TurnRecord:
    """Build a minimal real TurnRecord."""
    return TurnRecord(
        turn_number=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=0.0,
        finish_reason=finish_reason,
        latency_ms=latency_ms,
    )


def _execution_result(*turns: TurnRecord) -> ExecutionResult:
    """Build a real ExecutionResult carrying the given turns.

    A genuine ``ExecutionResult`` (real ``AgentContext`` + real
    ``TurnRecord``s) at the ``_collect`` boundary instead of a bare
    mock; the collector reads only ``turns`` but the full typed
    instance keeps the contract honest.
    """
    identity = AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-large-001"),
        hiring_date=date(2026, 1, 1),
    )
    return ExecutionResult(
        context=AgentContext.from_identity(identity),
        termination_reason=TerminationReason.COMPLETED,
        turns=tuple(turns),
    )


async def _collect(  # noqa: PLR0913
    collector: CoordinationMetricsCollector,
    *,
    execution_result: ExecutionResult,
    agent_id: str,
    task_id: str,
    team_size: int = 1,
    agent_durations: tuple[tuple[str, float], ...] | None = None,
    agent_outputs: tuple[str, ...] | None = None,
    is_multi_agent: bool = False,
) -> CoordinationMetrics:
    """Invoke ``collect`` with the kwargs bundled into ``CollectionInputs``."""
    return await collector.collect(
        CollectionInputs(
            execution_result=execution_result,
            agent_id=agent_id,
            task_id=task_id,
            team_size=team_size,
            agent_durations=agent_durations,
            agent_outputs=agent_outputs,
            is_multi_agent=is_multi_agent,
        ),
    )


def _cost_tracker() -> MagicMock:
    return MagicMock()


def _baseline_store(
    *,
    turns: float = 5.0,
    error_rate: float = 0.1,
    total_tokens: float = 1000.0,
    duration_seconds: float = 10.0,
    pre_populated: bool = True,
) -> BaselineStore:
    store = BaselineStore(window_size=50)
    if pre_populated:
        store.record(
            BaselineRecord(
                agent_id="test-sas-agent",
                task_id="test-task",
                turns=turns,
                error_rate=error_rate,
                total_tokens=total_tokens,
                duration_seconds=duration_seconds,
            )
        )
    return store


def _mock_bus(message_counts: dict[str, int] | None = None) -> AsyncMock:
    """Build a mock MessageBus returning channels with specified message counts."""
    bus = AsyncMock()
    if message_counts is None:
        message_counts = {}
    channels = []
    for ch_name in message_counts:
        ch = MagicMock()
        ch.name = ch_name
        channels.append(ch)

    # Configure list_channels to return the channels
    bus.list_channels = AsyncMock(return_value=tuple(channels))

    # Configure get_channel_history based on channel name
    async def _get_history(ch_name: str, /, **__: object) -> tuple[object, ...]:
        return tuple(MagicMock() for _ in range(message_counts.get(ch_name, 0)))

    bus.get_channel_history = AsyncMock(side_effect=_get_history)
    return bus


def _mock_similarity_computer(scores: tuple[float, ...] = (0.8, 0.6)) -> AsyncMock:
    computer = AsyncMock(spec=SimilarityComputer)
    computer.compute_pairwise_similarity = AsyncMock(return_value=scores)
    return computer


def _mock_dispatcher() -> AsyncMock:
    return AsyncMock()


# ---------------------------------------------------------------------------
# config.enabled=False
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDisabledCollection:
    """When config.enabled=False, collect() returns empty CoordinationMetrics."""

    async def test_disabled_returns_empty_metrics(self) -> None:
        collector = CoordinationMetricsCollector(
            config=_config(enabled=False),
            cost_tracker=_cost_tracker(),
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="agent-1",
            task_id="task-1",
            is_multi_agent=True,
        )
        assert isinstance(result, CoordinationMetrics)
        assert result.efficiency is None
        assert result.overhead is None
        assert result.error_amplification is None
        assert result.message_density is None
        assert result.redundancy_rate is None

    async def test_disabled_never_records_baseline(self) -> None:
        store = _baseline_store(pre_populated=False)
        collector = CoordinationMetricsCollector(
            config=_config(enabled=False),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
        )
        await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="agent-1",
            task_id="task-1",
            is_multi_agent=False,
        )
        assert len(store) == 0


# ---------------------------------------------------------------------------
# Single-agent runs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSingleAgentRun:
    """Single-agent runs record baseline and return empty metrics."""

    async def test_records_baseline_for_single_agent(self) -> None:
        store = _baseline_store(pre_populated=False)
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
        )
        await _collect(
            collector,
            execution_result=_execution_result(_turn(), _turn()),
            agent_id="agent-1",
            task_id="task-1",
            is_multi_agent=False,
        )
        assert len(store) == 1
        assert store.get_baseline_turns() == pytest.approx(2.0)

    async def test_single_agent_returns_empty_metrics(self) -> None:
        store = _baseline_store(pre_populated=False)
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="agent-1",
            task_id="task-1",
            is_multi_agent=False,
        )
        assert isinstance(result, CoordinationMetrics)
        assert result.efficiency is None
        assert result.overhead is None

    async def test_single_agent_no_store_returns_empty(self) -> None:
        """No baseline_store -> no recording, still returns empty metrics."""
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            baseline_store=None,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="agent-1",
            task_id="task-1",
            is_multi_agent=False,
        )
        assert isinstance(result, CoordinationMetrics)

    async def test_error_turns_counted_in_baseline_rate(self) -> None:
        """Error turns are reflected in the recorded error_rate baseline."""
        store = _baseline_store(pre_populated=False)
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
        )
        turns = (
            _turn(FinishReason.STOP),
            _turn(FinishReason.ERROR),
            _turn(FinishReason.CONTENT_FILTER),
        )
        await _collect(
            collector,
            execution_result=_execution_result(*turns),
            agent_id="agent-1",
            task_id="task-1",
            is_multi_agent=False,
        )
        # 2 error turns out of 3 total -> error_rate = 2/3
        assert store.get_baseline_error_rate() == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# Metric dependencies -- skipped when missing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMissingDependencies:
    """Metrics requiring missing dependencies return None."""

    async def test_efficiency_none_without_baseline_store(self) -> None:
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            baseline_store=None,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        assert result.efficiency is None

    async def test_overhead_none_without_baseline_store(self) -> None:
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            baseline_store=None,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        assert result.overhead is None

    async def test_efficiency_none_when_baseline_empty(self) -> None:
        store = _baseline_store(pre_populated=False)
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        assert result.efficiency is None

    async def test_message_density_none_without_bus(self) -> None:
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            message_bus=None,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        assert result.message_density is None

    async def test_redundancy_rate_none_without_similarity_computer(self) -> None:
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            similarity_computer=None,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
            agent_outputs=("output A", "output B"),
        )
        assert result.redundancy_rate is None

    async def test_redundancy_rate_none_with_single_output(self) -> None:
        """Redundancy needs at least 2 outputs to be meaningful."""
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            similarity_computer=_mock_similarity_computer(),
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
            agent_outputs=("only one output",),
        )
        assert result.redundancy_rate is None

    async def test_amdahl_ceiling_none_for_team_size_one(self) -> None:
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
            team_size=1,
        )
        assert result.amdahl_ceiling is None

    async def test_straggler_gap_none_without_durations(self) -> None:
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
            agent_durations=None,
        )
        assert result.straggler_gap is None

    async def test_message_overhead_none_when_density_missing(self) -> None:
        """MessageOverhead is derived from MessageDensity; none if bus absent."""
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            message_bus=None,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        assert result.message_overhead is None


# ---------------------------------------------------------------------------
# Selective collection via config.collect
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelectiveCollection:
    """Only metrics in config.collect are computed."""

    async def test_only_overhead_in_collect(self) -> None:
        store = _baseline_store()
        collector = CoordinationMetricsCollector(
            config=_config(collect=(CoordinationMetricName.OVERHEAD,)),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn(), _turn(), _turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        # Only overhead should be computed
        assert result.overhead is not None
        assert result.efficiency is None
        assert result.error_amplification is None
        assert result.message_density is None
        assert result.redundancy_rate is None


# ---------------------------------------------------------------------------
# Metrics actually computed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetricComputation:
    """Verify computed metric values are plausible."""

    async def test_efficiency_computed_with_baseline(self) -> None:
        # Baseline: 5 turns SAS; MAS: 5 turns -> Ec = 1.0 / (5/5) = 1.0
        store = _baseline_store(turns=5.0)
        collector = CoordinationMetricsCollector(
            config=_config(collect=(CoordinationMetricName.EFFICIENCY,)),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
        )
        t1, t2, t3, t4, t5 = (_turn() for _ in range(5))
        result = await _collect(
            collector,
            execution_result=_execution_result(t1, t2, t3, t4, t5),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        assert result.efficiency is not None
        assert result.efficiency.value == pytest.approx(1.0)

    async def test_overhead_positive_when_mas_exceeds_sas(self) -> None:
        # SAS baseline: 4 turns; MAS: 8 turns -> O% = (8-4)/4*100 = 100%
        store = _baseline_store(turns=4.0)
        collector = CoordinationMetricsCollector(
            config=_config(collect=(CoordinationMetricName.OVERHEAD,)),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
        )
        turns = tuple(_turn() for _ in range(8))
        result = await _collect(
            collector,
            execution_result=_execution_result(*turns),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        assert result.overhead is not None
        assert result.overhead.value_percent == pytest.approx(100.0)

    async def test_error_amplification_computed(self) -> None:
        store = _baseline_store(error_rate=0.1)
        collector = CoordinationMetricsCollector(
            config=_config(collect=(CoordinationMetricName.ERROR_AMPLIFICATION,)),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
        )
        # 1 error turn out of 2 -> error_rate_mas = 0.5
        result = await _collect(
            collector,
            execution_result=_execution_result(
                _turn(FinishReason.STOP), _turn(FinishReason.ERROR)
            ),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        assert result.error_amplification is not None
        assert result.error_amplification.value == pytest.approx(5.0)  # 0.5/0.1

    async def test_error_amplification_none_when_sas_error_rate_zero(self) -> None:
        store = _baseline_store(error_rate=0.0)
        collector = CoordinationMetricsCollector(
            config=_config(collect=(CoordinationMetricName.ERROR_AMPLIFICATION,)),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn(FinishReason.ERROR)),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        # Cannot divide by zero error rate
        assert result.error_amplification is None

    async def test_message_density_computed_with_bus(self) -> None:
        bus = _mock_bus({"#team": 6, "#ops": 4})
        collector = CoordinationMetricsCollector(
            config=_config(collect=(CoordinationMetricName.MESSAGE_DENSITY,)),
            cost_tracker=_cost_tracker(),
            message_bus=bus,
        )
        # 5 turns, 10 messages -> c = 10/5 = 2.0
        turns = tuple(_turn() for _ in range(5))
        result = await _collect(
            collector,
            execution_result=_execution_result(*turns),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        assert result.message_density is not None
        assert result.message_density.value == pytest.approx(2.0)

    async def test_redundancy_rate_computed(self) -> None:
        computer = _mock_similarity_computer(scores=(0.8, 0.6, 0.7))
        collector = CoordinationMetricsCollector(
            config=_config(collect=(CoordinationMetricName.REDUNDANCY,)),
            cost_tracker=_cost_tracker(),
            similarity_computer=computer,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
            agent_outputs=("output A", "output B", "output C"),
        )
        assert result.redundancy_rate is not None
        # mean(0.8, 0.6, 0.7) = 0.7
        assert result.redundancy_rate.value == pytest.approx(0.7)

    async def test_amdahl_ceiling_computed_for_large_team(self) -> None:
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
            team_size=4,
        )
        assert result.amdahl_ceiling is not None
        # p = (4-1)/4 = 0.75, ceiling = 1/(1-0.75) = 4.0
        assert result.amdahl_ceiling.max_speedup == pytest.approx(4.0)

    async def test_straggler_gap_computed(self) -> None:
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
        )
        durations = (("agent-1", 10.0), ("agent-2", 20.0))
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
            agent_durations=durations,
        )
        assert result.straggler_gap is not None
        # gap = slowest(20) - mean(15) = 5 seconds
        assert result.straggler_gap.gap_seconds == pytest.approx(5.0)

    async def test_message_overhead_derived_from_density(self) -> None:
        bus = _mock_bus({"#team": 8})
        collector = CoordinationMetricsCollector(
            config=_config(
                collect=(
                    CoordinationMetricName.MESSAGE_DENSITY,
                    CoordinationMetricName.MESSAGE_OVERHEAD,
                ),
            ),
            cost_tracker=_cost_tracker(),
            message_bus=bus,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
            team_size=2,
        )
        # message_density was computed, so message_overhead should also be present
        assert result.message_overhead is not None


# ---------------------------------------------------------------------------
# Alert dispatching
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAlertDispatching:
    """Alerts are fired when orchestration overhead crosses thresholds."""

    async def test_alert_dispatched_when_warn_threshold_crossed(self) -> None:
        # O% > warn(50%) should fire a WARNING alert
        # SAS: 4 turns; MAS: 12 turns -> O% = (12-4)/4*100 = 200% >> warn
        store = _baseline_store(turns=4.0)
        dispatcher = _mock_dispatcher()
        collector = CoordinationMetricsCollector(
            config=_config(
                collect=(CoordinationMetricName.OVERHEAD,),
                info=0.10,
                warn=0.50,
                critical=0.90,
            ),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
            notification_dispatcher=dispatcher,
        )
        turns = tuple(_turn() for _ in range(12))
        await _collect(
            collector,
            execution_result=_execution_result(*turns),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        dispatcher.dispatch.assert_awaited_once()

    async def test_no_alert_below_info_threshold(self) -> None:
        # SAS: 10 turns; MAS: 10 turns -> O% = 0% (below info=0.10)
        store = _baseline_store(turns=10.0)
        dispatcher = _mock_dispatcher()
        collector = CoordinationMetricsCollector(
            config=_config(collect=(CoordinationMetricName.OVERHEAD,)),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
            notification_dispatcher=dispatcher,
        )
        turns = tuple(_turn() for _ in range(10))
        await _collect(
            collector,
            execution_result=_execution_result(*turns),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        dispatcher.dispatch.assert_not_awaited()

    async def test_no_alert_when_no_dispatcher(self) -> None:
        """No crash when notification_dispatcher is None."""
        store = _baseline_store(turns=4.0)
        collector = CoordinationMetricsCollector(
            config=_config(collect=(CoordinationMetricName.OVERHEAD,)),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
            notification_dispatcher=None,
        )
        turns = tuple(_turn() for _ in range(12))
        # Should not raise
        result = await _collect(
            collector,
            execution_result=_execution_result(*turns),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        assert isinstance(result, CoordinationMetrics)

    async def test_critical_alert_dispatched(self) -> None:
        # O% = 500% -> critical fraction = 5.0 >> critical=0.70
        store = _baseline_store(turns=2.0)
        dispatcher = _mock_dispatcher()
        collector = CoordinationMetricsCollector(
            config=_config(
                collect=(CoordinationMetricName.OVERHEAD,),
                info=0.10,
                warn=0.50,
                critical=0.70,
            ),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
            notification_dispatcher=dispatcher,
        )
        turns = tuple(_turn() for _ in range(12))  # 12 turns, SAS=2 -> O%=500%
        await _collect(
            collector,
            execution_result=_execution_result(*turns),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        dispatcher.dispatch.assert_awaited_once()
        # Verify the notification has critical severity
        notification = dispatcher.dispatch.call_args[0][0]
        assert notification.severity.value == "critical"


# ---------------------------------------------------------------------------
# Individual metric error isolation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetricIsolation:
    """Individual metric errors are logged but never block other metrics."""

    async def test_similarity_computer_error_isolated(self) -> None:
        """Redundancy failure does not block other metrics."""
        computer = AsyncMock(spec=SimilarityComputer)
        computer.compute_pairwise_similarity.side_effect = RuntimeError("embed fail")

        store = _baseline_store()
        collector = CoordinationMetricsCollector(
            config=_config(
                collect=(
                    CoordinationMetricName.OVERHEAD,
                    CoordinationMetricName.REDUNDANCY,
                ),
            ),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
            similarity_computer=computer,
        )
        turns = tuple(_turn() for _ in range(6))
        result = await _collect(
            collector,
            execution_result=_execution_result(*turns),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
            agent_outputs=("out A", "out B"),
        )
        # Redundancy should be None due to error
        assert result.redundancy_rate is None
        # Overhead should still be computed
        assert result.overhead is not None

    async def test_bus_error_isolated(self) -> None:
        """Message bus failure does not block other metrics."""
        bus = AsyncMock(spec=MessageBus)
        bus.list_channels = AsyncMock(side_effect=RuntimeError("bus down"))

        store = _baseline_store()
        collector = CoordinationMetricsCollector(
            config=_config(
                collect=(
                    CoordinationMetricName.OVERHEAD,
                    CoordinationMetricName.MESSAGE_DENSITY,
                ),
            ),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
            message_bus=bus,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn(), _turn(), _turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        assert result.message_density is None
        assert result.overhead is not None

    async def test_returns_coordmetrics_on_all_failures(self) -> None:
        """collect() always returns CoordinationMetrics, even when all metrics fail."""
        # Empty baseline store -> no efficiency/overhead/error_amplification
        # No bus -> no message_density
        # No similarity_computer -> no redundancy
        # team_size=1 -> no amdahl
        # no durations -> no straggler/token_speedup
        store = _baseline_store(pre_populated=False)
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            baseline_store=store,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
            team_size=1,
        )
        assert isinstance(result, CoordinationMetrics)


# ---------------------------------------------------------------------------
# CoordinationMetricsStore write (collector owns the store)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStoreWrite:
    """The collector writes a CoordinationMetricsRecord for multi-agent runs."""

    async def test_multi_agent_writes_one_record(self) -> None:
        metrics_store = CoordinationMetricsStore()
        clock = FakeClock()
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            baseline_store=_baseline_store(),
            metrics_store=metrics_store,
            clock=clock,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn(), _turn(), _turn()),
            agent_id="lead-agent",
            task_id="task-42",
            team_size=3,
            is_multi_agent=True,
        )
        assert metrics_store.count() == 1
        records, total = metrics_store.query(limit=10)
        assert total == 1
        record = records[0]
        assert record.task_id == "task-42"
        # Multi-agent is a system-level run: no single lead agent.
        assert record.agent_id is None
        assert record.team_size == 3
        assert record.computed_at == clock.now()
        assert record.metrics is result

    async def test_single_agent_writes_no_record(self) -> None:
        metrics_store = CoordinationMetricsStore()
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            baseline_store=_baseline_store(pre_populated=False),
            metrics_store=metrics_store,
            clock=FakeClock(),
        )
        await _collect(
            collector,
            execution_result=_execution_result(_turn(), _turn()),
            agent_id="agent-1",
            task_id="task-1",
            is_multi_agent=False,
        )
        assert metrics_store.count() == 0

    async def test_no_store_does_not_raise(self) -> None:
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            baseline_store=_baseline_store(),
            metrics_store=None,
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn(), _turn()),
            agent_id="lead-agent",
            task_id="task-1",
            team_size=2,
            is_multi_agent=True,
        )
        assert isinstance(result, CoordinationMetrics)

    async def test_disabled_collector_writes_no_record(self) -> None:
        metrics_store = CoordinationMetricsStore()
        collector = CoordinationMetricsCollector(
            config=_config(enabled=False),
            cost_tracker=_cost_tracker(),
            baseline_store=_baseline_store(),
            metrics_store=metrics_store,
            clock=FakeClock(),
        )
        await _collect(
            collector,
            execution_result=_execution_result(_turn(), _turn()),
            agent_id="lead-agent",
            task_id="task-1",
            team_size=2,
            is_multi_agent=True,
        )
        assert metrics_store.count() == 0

    async def test_record_written_even_when_all_metrics_skipped(self) -> None:
        """A multi-agent run still records the (possibly sparse) metrics."""
        metrics_store = CoordinationMetricsStore()
        collector = CoordinationMetricsCollector(
            config=_config(),
            cost_tracker=_cost_tracker(),
            baseline_store=_baseline_store(pre_populated=False),
            metrics_store=metrics_store,
            clock=FakeClock(),
        )
        await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="lead-agent",
            task_id="task-1",
            team_size=1,
            is_multi_agent=True,
        )
        assert metrics_store.count() == 1
