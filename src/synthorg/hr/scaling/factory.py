"""Scaling service factory.

Assembles a fully wired ScalingService from configuration
and injected dependencies.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, assert_never

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.types import NotBlankStr
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.offboarding_service import OffboardingService
from synthorg.hr.pruning.policy import (
    PruningPolicy,
    ThresholdPruningPolicy,
    ThresholdPruningPolicyConfig,
)
from synthorg.hr.scaling.config import ScalingConfig, TriggerConfig
from synthorg.hr.scaling.context import ScalingContextBuilder
from synthorg.hr.scaling.guards.approval_gate import ApprovalGateGuard
from synthorg.hr.scaling.guards.composite import CompositeScalingGuard
from synthorg.hr.scaling.guards.conflict_resolver import ConflictResolver
from synthorg.hr.scaling.guards.cooldown import CooldownGuard
from synthorg.hr.scaling.guards.rate_limit import RateLimitGuard
from synthorg.hr.scaling.protocols import (
    ScalingGuard,
    ScalingStrategy,
    ScalingTrigger,
)
from synthorg.hr.scaling.service import AgentLookup, ScalingService
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
from synthorg.hr.scaling.triggers.composite import CompositeScalingTrigger
from synthorg.hr.scaling.triggers.threshold import SignalThresholdTrigger
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
                evolution_check_timeout_seconds=(
                    config.performance_pruning.evolution_check_timeout_seconds
                ),
            ),
        )
    elif config.performance_pruning.enabled:
        logger.debug(
            HR_SCALING_FACTORY_ASSEMBLED,
            component="strategies",
            note="performance_pruning enabled but skipped (pruning_policy absent)",
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
    workload_src = (
        WorkloadSignalSource(max_concurrent_tasks=config.workload.max_concurrent_tasks)
        if config.workload.enabled
        else None
    )
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

    active_sources = [
        name
        for name, src in (
            ("workload", workload_src),
            ("budget", budget_src),
            ("skill", skill_src),
            ("performance", performance_src),
            ("benchmark", benchmark_src),
        )
        if src is not None
    ]
    logger.debug(
        HR_SCALING_FACTORY_ASSEMBLED,
        component="context_builder",
        sources=active_sources,
    )
    return ScalingContextBuilder(
        workload_source=workload_src,
        budget_source=budget_src,
        performance_source=performance_src,
        skill_source=skill_src,
        benchmark_source=benchmark_src,
    )


def _build_batched(triggers: TriggerConfig) -> BatchedScalingTrigger:
    """Build the time-interval batched trigger.

    Returns:
        A configured :class:`BatchedScalingTrigger`.
    """
    return BatchedScalingTrigger(interval_seconds=triggers.batched_interval_seconds)


def _build_signal_threshold(triggers: TriggerConfig) -> SignalThresholdTrigger:
    """Build the signal-threshold trigger.

    Returns:
        A configured :class:`SignalThresholdTrigger`.
    """
    return SignalThresholdTrigger(
        signal_name=triggers.signal_name,
        threshold=triggers.signal_threshold,
        above=triggers.signal_above,
    )


def _build_leaf_trigger(
    member: Literal["batched", "signal_threshold"],
    triggers: TriggerConfig,
) -> ScalingTrigger:
    """Build a single leaf trigger for the composite.

    Returns:
        The leaf :class:`ScalingTrigger`.
    """
    if member == "batched":
        return _build_batched(triggers)
    return _build_signal_threshold(triggers)


def create_scaling_trigger(config: ScalingConfig) -> ScalingTrigger:
    """Create the trigger selected by ``config.triggers.type``.

    The ``batched`` default reproduces the historical time-interval
    trigger exactly. ``signal_threshold`` fires on a signal crossing
    (primed via ``ScalingService.update_signal``), and ``composite``
    combines the configured leaf triggers with OR semantics.

    Args:
        config: Scaling configuration.

    Returns:
        The configured :class:`ScalingTrigger`.
    """
    triggers = config.triggers
    match triggers.type:
        case "batched":
            trigger: ScalingTrigger = _build_batched(triggers)
        case "signal_threshold":
            trigger = _build_signal_threshold(triggers)
        case "composite":
            trigger = CompositeScalingTrigger(
                triggers=tuple(
                    _build_leaf_trigger(member, triggers)
                    for member in triggers.composite_members
                ),
            )
        case _:  # pragma: no cover
            assert_never(triggers.type)
    logger.debug(
        HR_SCALING_FACTORY_ASSEMBLED,
        component="trigger",
        trigger_type=triggers.type,
        name=str(trigger.name),
    )
    return trigger


def build_scaling_service(
    config: ScalingConfig,
    *,
    hiring_service: HiringService | None = None,
    offboarding_service: OffboardingService | None = None,
    agent_registry: AgentLookup | None = None,
    approval_store: ApprovalStoreProtocol | None = None,
    pruning_policy: PruningPolicy | None = None,
    evolution_checker: Callable[[NotBlankStr], Awaitable[bool]] | None = None,
    benchmark_history_dir: Path | None = None,
) -> ScalingService:
    """Assemble a fully wired :class:`ScalingService` from configuration.

    Combines the strategy/guard/context/trigger sub-factories with the
    hire / offboard execution collaborators into the orchestrating service
    the boot wiring publishes on ``HrStateSlice.scaling_service``.

    When ``config.performance_pruning`` is enabled and no ``pruning_policy``
    is supplied, a default :class:`ThresholdPruningPolicy` is wired so the
    performance-pruning strategy can evaluate rather than being silently
    skipped (it cannot run without a policy).

    Args:
        config: Scaling configuration (strategy/guard/trigger knobs).
        hiring_service: Hiring service that executes HIRE decisions.
        offboarding_service: Offboarding service that executes PRUNE
            decisions.
        agent_registry: Agent lookup used to resolve target names when
            executing a PRUNE.
        approval_store: Approval store wired into the approval-gate guard.
            When ``None`` the approval gate is intentionally omitted (the
            decisions then flow through only the conflict / cooldown / rate
            guards); the boot wiring never passes ``None`` -- it requires a
            wired approval store before constructing the service -- so this
            applies to tests and custom harnesses only.
        pruning_policy: Policy backing the performance-pruning strategy.
            Defaults to a :class:`ThresholdPruningPolicy` when omitted and
            performance pruning is enabled.
        evolution_checker: Async predicate the performance-pruning strategy
            consults to defer pruning of agents under active evolution.
        benchmark_history_dir: Golden-benchmark scorecard directory; when
            provided a benchmark regression surfaces into the scaling
            context.

    Returns:
        The assembled :class:`ScalingService`.
    """
    effective_policy = pruning_policy
    if effective_policy is None and config.performance_pruning.enabled:
        effective_policy = ThresholdPruningPolicy(ThresholdPruningPolicyConfig())
        logger.debug(
            HR_SCALING_FACTORY_ASSEMBLED,
            component="pruning_policy",
            note="default ThresholdPruningPolicy applied (no policy supplied)",
        )

    strategies = create_scaling_strategies(
        config,
        pruning_policy=effective_policy,
        evolution_checker=evolution_checker,
    )
    guard = create_scaling_guards(config, approval_store=approval_store)
    context_builder = create_scaling_context_builder(
        config,
        benchmark_history_dir=benchmark_history_dir,
    )
    trigger = create_scaling_trigger(config)

    service = ScalingService(
        strategies=strategies,
        trigger=trigger,
        guard=guard,
        context_builder=context_builder,
        config=config,
        hiring_service=hiring_service,
        offboarding_service=offboarding_service,
        agent_registry=agent_registry,
    )
    logger.info(
        HR_SCALING_FACTORY_ASSEMBLED,
        component="service",
        strategies=len(strategies),
        has_hiring=hiring_service is not None,
        has_offboarding=offboarding_service is not None,
    )
    return service
