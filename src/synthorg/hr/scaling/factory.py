"""Scaling service factory.

Assembles a fully wired ScalingService from configuration
and injected dependencies.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.types import NotBlankStr
from synthorg.hr.pruning.policy import PruningPolicy
from synthorg.hr.scaling.config import ScalingConfig
from synthorg.hr.scaling.context import ScalingContextBuilder
from synthorg.hr.scaling.guards.approval_gate import ApprovalGateGuard
from synthorg.hr.scaling.guards.composite import CompositeScalingGuard
from synthorg.hr.scaling.guards.conflict_resolver import ConflictResolver
from synthorg.hr.scaling.guards.cooldown import CooldownGuard
from synthorg.hr.scaling.guards.rate_limit import RateLimitGuard
from synthorg.hr.scaling.protocols import ScalingGuard, ScalingStrategy
from synthorg.hr.scaling.signals.benchmark import BenchmarkSignalSource
from synthorg.hr.scaling.signals.budget import BudgetSignalSource
from synthorg.hr.scaling.signals.performance import PerformanceSignalSource
from synthorg.hr.scaling.signals.skill import SkillSignalSource
from synthorg.hr.scaling.signals.workload import WorkloadSignalSource
from synthorg.hr.scaling.strategies.budget_cap import BudgetCapStrategy
from synthorg.hr.scaling.strategies.performance_pruning import (
    PerformancePruningStrategy,
)
from synthorg.hr.scaling.strategies.skill_gap import SkillGapStrategy
from synthorg.hr.scaling.strategies.workload import (
    WorkloadAutoScaleStrategy,
)
from synthorg.hr.scaling.triggers.batched import BatchedScalingTrigger
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_FACTORY_ASSEMBLED

logger = get_logger(__name__)


def create_scaling_strategies(
    config: ScalingConfig,
    *,
    pruning_policy: PruningPolicy | None = None,
    evolution_checker: Callable[[NotBlankStr], Awaitable[bool]] | None = None,
) -> tuple[ScalingStrategy, ...]:
    """Create enabled strategies from configuration.

    Args:
        config: Scaling configuration.
        pruning_policy: Optional ``PruningPolicy`` to wire into the
            ``PerformancePruningStrategy``. When omitted, the
            performance pruning strategy is skipped even if enabled
            in config -- it cannot evaluate without a policy.
        evolution_checker: Optional async callable that reports
            whether an agent has recent evolution adaptations. Passed
            through to ``PerformancePruningStrategy`` so it can defer
            pruning of agents currently being adapted.

    Returns:
        Tuple of enabled strategy instances.
    """
    strategies: list[ScalingStrategy] = []

    if config.workload.enabled:
        strategies.append(
            WorkloadAutoScaleStrategy(
                hire_threshold=config.workload.hire_threshold,
                prune_threshold=config.workload.prune_threshold,
            ),
        )

    if config.budget_cap.enabled:
        strategies.append(
            BudgetCapStrategy(
                safety_margin=config.budget_cap.safety_margin,
                headroom_fraction=config.budget_cap.headroom_fraction,
            ),
        )

    if config.skill_gap.enabled:
        strategies.append(
            SkillGapStrategy(
                enabled=True,
                min_missing_skills=config.skill_gap.min_missing_skills,
            ),
        )

    if config.performance_pruning.enabled and pruning_policy is not None:
        strategies.append(
            PerformancePruningStrategy(
                policy=pruning_policy,
                evolution_checker=evolution_checker,
                defer_during_evolution=(
                    config.performance_pruning.defer_during_evolution
                ),
            ),
        )

    logger.debug(
        HR_SCALING_FACTORY_ASSEMBLED,
        component="strategies",
        count=len(strategies),
        names=[str(s.name) for s in strategies],
    )
    return tuple(strategies)


def create_scaling_guards(
    config: ScalingConfig,
    *,
    approval_store: ApprovalStoreProtocol | None = None,
) -> ScalingGuard:
    """Create the guard chain from configuration.

    Args:
        config: Scaling configuration.
        approval_store: Optional approval store for the approval gate.

    Returns:
        A CompositeScalingGuard or single guard.
    """
    priority_map = {name.value: idx for idx, name in enumerate(config.priority_order)}

    guards: list[ScalingGuard] = [
        ConflictResolver(priority=priority_map),
        CooldownGuard(cooldown_seconds=config.guards.cooldown_seconds),
        RateLimitGuard(
            max_hires_per_day=config.guards.max_hires_per_day,
            max_prunes_per_day=config.guards.max_prunes_per_day,
        ),
    ]

    if approval_store is not None:
        guards.append(
            ApprovalGateGuard(
                approval_store=approval_store,
                expiry_days=config.guards.approval_expiry_days,
            ),
        )

    composite = CompositeScalingGuard(guards=tuple(guards))
    logger.debug(
        HR_SCALING_FACTORY_ASSEMBLED,
        component="guards",
        count=len(guards),
        names=[str(g.name) for g in guards],
    )
    return composite


def create_scaling_context_builder(
    config: ScalingConfig,
    *,
    benchmark_history_dir: Path | None = None,
) -> ScalingContextBuilder:
    """Create the context builder from configuration.

    Args:
        config: Scaling configuration.
        benchmark_history_dir: Directory the golden benchmark records its
            per-run scorecard summaries into (``meta.scorecard_history_dir``).
            When provided, a ``BenchmarkSignalSource`` is wired so a benchmark
            regression surfaces into the scaling context; when ``None`` (no
            benchmark configured) the benchmark signal is simply absent.

    Returns:
        Configured ScalingContextBuilder.
    """
    workload_src = WorkloadSignalSource() if config.workload.enabled else None
    budget_src = BudgetSignalSource() if config.budget_cap.enabled else None
    skill_src = SkillSignalSource() if config.skill_gap.enabled else None
    performance_src = (
        PerformanceSignalSource() if config.performance_pruning.enabled else None
    )
    benchmark_src = (
        BenchmarkSignalSource(benchmark_history_dir)
        if benchmark_history_dir is not None
        else None
    )

    return ScalingContextBuilder(
        workload_source=workload_src,
        budget_source=budget_src,
        performance_source=performance_src,
        skill_source=skill_src,
        benchmark_source=benchmark_src,
    )


def create_scaling_trigger(
    config: ScalingConfig,
) -> BatchedScalingTrigger:
    """Create the trigger from configuration.

    Args:
        config: Scaling configuration.

    Returns:
        Configured trigger.
    """
    return BatchedScalingTrigger(
        interval_seconds=config.triggers.batched_interval_seconds,
    )
