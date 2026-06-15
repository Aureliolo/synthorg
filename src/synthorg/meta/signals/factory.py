# module-kind: code
"""Factory composing the SignalsService from live runtime collaborators.

Assembles the seven per-domain aggregators, the snapshot builder, and the
approval store into one :class:`SignalsService`. The scaling domain and
the error / evolution / telemetry stores are optional: when their backing
service or store is not wired they degrade to an empty per-domain summary
rather than blocking the whole facade, so the signals MCP handlers and
``/meta/chat`` signal reads come online with whatever data is available.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.engine.classification.taxonomy_store_protocol import ErrorTaxonomyStore
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.scaling.service import ScalingService
from synthorg.meta.evolution.outcome_store_protocol import EvolutionOutcomeStore
from synthorg.meta.signals.benchmark import BenchmarkSignalAggregator
from synthorg.meta.signals.budget import BudgetSignalAggregator
from synthorg.meta.signals.coordination import CoordinationSignalAggregator
from synthorg.meta.signals.errors import ErrorSignalAggregator
from synthorg.meta.signals.evolution import EvolutionSignalAggregator
from synthorg.meta.signals.performance import PerformanceSignalAggregator
from synthorg.meta.signals.scaling import ScalingSignalAggregator
from synthorg.meta.signals.service import SignalsService
from synthorg.meta.signals.snapshot import SnapshotBuilder
from synthorg.meta.signals.telemetry import TelemetrySignalAggregator
from synthorg.telemetry.event_counter_protocol import TelemetryEventCounter


def _empty_cost_records() -> tuple[object, ...]:
    """Placeholder cost-record provider (the budget aggregator is a stub).

    Returns:
        An empty tuple; the budget aggregator ignores its providers until
        the real implementation lands.
    """
    return ()


def build_signals_service(  # noqa: PLR0913 -- keyword-only collaborator DI
    *,
    performance_tracker: PerformanceTracker,
    agent_ids_provider: Callable[[], Sequence[str]],
    approval_store: ApprovalStoreProtocol,
    scaling_service: ScalingService | None = None,
    error_store: ErrorTaxonomyStore | None = None,
    evolution_store: EvolutionOutcomeStore | None = None,
    telemetry_counter: TelemetryEventCounter | None = None,
    budget_total_monthly: float = 0.0,
    benchmark_history_dir: Path | None = None,
) -> SignalsService:
    """Compose a :class:`SignalsService` from live runtime collaborators.

    Args:
        performance_tracker: Per-agent performance snapshots source.
        agent_ids_provider: Callable returning the current active agent
            ids the performance aggregator iterates.
        approval_store: Shared store backing proposal submit / list.
        scaling_service: Scaling service whose decision history feeds the
            scaling aggregator; ``None`` degrades the scaling domain to
            an empty summary.
        error_store: Optional error-taxonomy store.
        evolution_store: Optional evolution-outcome store.
        telemetry_counter: Optional telemetry event counter.
        budget_total_monthly: Monthly budget ceiling for the budget
            aggregator.
        benchmark_history_dir: Optional golden-benchmark history dir.

    Returns:
        A fully wired :class:`SignalsService`.
    """
    performance = PerformanceSignalAggregator(
        tracker=performance_tracker,
        agent_ids_provider=agent_ids_provider,
    )
    budget = BudgetSignalAggregator(
        cost_record_provider=_empty_cost_records,
        budget_total_monthly=budget_total_monthly,
    )
    coordination = CoordinationSignalAggregator()
    scaling = (
        ScalingSignalAggregator(service=scaling_service)
        if scaling_service is not None
        else None
    )
    errors = ErrorSignalAggregator(error_store)
    evolution = EvolutionSignalAggregator(evolution_store)
    telemetry = TelemetrySignalAggregator(telemetry_counter)
    benchmark = BenchmarkSignalAggregator(benchmark_history_dir)

    snapshot_builder = SnapshotBuilder(
        performance=performance,
        budget=budget,
        coordination=coordination,
        scaling=scaling,
        errors=errors,
        evolution=evolution,
        telemetry=telemetry,
        benchmark=benchmark,
    )
    return SignalsService(
        performance=performance,
        budget=budget,
        coordination=coordination,
        scaling=scaling,
        errors=errors,
        evolution=evolution,
        telemetry=telemetry,
        snapshot_builder=snapshot_builder,
        approval_store=approval_store,
    )
