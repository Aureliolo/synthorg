"""Factory for building the evolution service from config."""

from typing import TYPE_CHECKING

from synthorg.core.registry import StrategyRegistry
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationProposal,  # used by _NoOpProposer
)
from synthorg.engine.identity.store.factory import build_identity_store
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.evolution.config import (
        EvolutionConfig,
        ShadowEvaluationConfig,
    )
    from synthorg.engine.evolution.guards.shadow_protocol import (
        ShadowAgentRunner,
        ShadowTaskProvider,
    )
    from synthorg.engine.evolution.guards.shadow_providers import (
        TaskSampler,
    )
    from synthorg.engine.evolution.protocols import (
        AdaptationAdapter,
        AdaptationGuard,
        AdaptationProposer,
        EvolutionTrigger,
    )
    from synthorg.engine.evolution.service import EvolutionService
    from synthorg.engine.identity.store.protocol import (
        IdentityVersionStore,
    )
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.versioning.service import VersioningService

logger = get_logger(__name__)


def build_evolution_service(  # noqa: PLR0913
    config: EvolutionConfig,
    *,
    registry: AgentRegistryService,
    versioning: VersioningService[AgentIdentity],
    tracker: PerformanceTracker,
    memory_backend: MemoryBackend | None = None,
    provider: CompletionProvider | None = None,
    shadow_runner: ShadowAgentRunner | None = None,
    shadow_task_sampler: TaskSampler | None = None,
) -> EvolutionService:
    """Build a fully wired ``EvolutionService`` from configuration.

    Args:
        config: Evolution configuration.
        registry: Agent registry.
        versioning: Versioning service for AgentIdentity.
        tracker: Performance tracker.
        memory_backend: Memory backend (optional).
        provider: LLM completion provider (for proposers).
        shadow_runner: Required when ``config.guards.shadow_evaluation`` is
            set; executes a single probe task against an identity and
            proposal.  In production this wraps ``AgentEngine.run``.
        shadow_task_sampler: Required when shadow evaluation is enabled
            *and* ``task_provider == "recent_history"``.  Returns recent
            completed tasks for the agent.

    Returns:
        Configured evolution service.

    Raises:
        ValueError: If shadow evaluation is enabled without the required
            dependencies.
    """
    from synthorg.engine.evolution.service import (  # noqa: PLC0415
        EvolutionService,
    )

    identity_store = build_identity_store(
        config.identity_store,
        registry=registry,
        versioning=versioning,
    )

    trigger = _build_trigger(config, tracker=tracker)
    proposer = _build_proposer(config, provider=provider)
    guard = _build_guard(
        config,
        identity_store=identity_store,
        shadow_runner=shadow_runner,
        shadow_task_sampler=shadow_task_sampler,
    )
    adapters = _build_adapters(
        config,
        identity_store=identity_store,
        memory_backend=memory_backend,
    )

    return EvolutionService(
        identity_store=identity_store,
        tracker=tracker,
        trigger=trigger,
        proposer=proposer,
        guard=guard,
        adapters=adapters,
        memory_backend=memory_backend,
        config=config,
    )


def _build_batched_trigger(
    config: EvolutionConfig,
    *,
    tracker: PerformanceTracker,
) -> EvolutionTrigger:
    del tracker  # batched trigger does not interact with the tracker
    from synthorg.engine.evolution.triggers.batched import (  # noqa: PLC0415
        BatchedTrigger,
    )

    return BatchedTrigger(
        interval_seconds=config.triggers.batched_interval_seconds,
    )


def _build_inflection_trigger(
    config: EvolutionConfig,
    *,
    tracker: PerformanceTracker,
) -> EvolutionTrigger:
    del config  # inflection trigger has no configurable params
    from synthorg.engine.evolution.triggers.inflection import (  # noqa: PLC0415
        InflectionTrigger,
    )

    trigger = InflectionTrigger()
    # Wire the trigger as the tracker's inflection sink if the
    # tracker doesn't already have one.
    if tracker.inflection_sink is None:
        tracker.inflection_sink = trigger
    return trigger


def _build_per_task_trigger(
    config: EvolutionConfig,
    *,
    tracker: PerformanceTracker,
) -> EvolutionTrigger:
    del tracker
    from synthorg.engine.evolution.triggers.per_task import (  # noqa: PLC0415
        PerTaskTrigger,
    )

    return PerTaskTrigger(
        min_tasks_since_last=config.triggers.per_task_min_tasks,
    )


_TRIGGER_REGISTRY: StrategyRegistry[EvolutionTrigger] = StrategyRegistry(
    {
        "batched": _build_batched_trigger,
        "inflection": _build_inflection_trigger,
        "per_task": _build_per_task_trigger,
    },
    kind="evolution_trigger",
)


def _build_trigger(
    config: EvolutionConfig,
    *,
    tracker: PerformanceTracker,
) -> EvolutionTrigger:
    """Build trigger from config.

    Multi-select: ``config.triggers.types`` may list one or more
    discriminators. Each maps to a registered per-type builder; the
    results are composed via ``CompositeTrigger`` when more than one
    trigger is enabled. An empty list falls back to a default
    ``BatchedTrigger``.
    """
    from synthorg.engine.evolution.triggers.batched import (  # noqa: PLC0415
        BatchedTrigger,
    )
    from synthorg.engine.evolution.triggers.composite import (  # noqa: PLC0415
        CompositeTrigger,
    )

    triggers: list[EvolutionTrigger] = [
        _TRIGGER_REGISTRY.build(t, config, tracker=tracker)
        for t in config.triggers.types
    ]
    if not triggers:
        triggers.append(BatchedTrigger())
    if len(triggers) == 1:
        return triggers[0]
    return CompositeTrigger(triggers=tuple(triggers))


def _build_self_report(
    config: EvolutionConfig,
    *,
    provider: CompletionProvider,
) -> AdaptationProposer:
    from synthorg.engine.evolution.proposers.self_report import (  # noqa: PLC0415
        SelfReportProposer,
    )

    return SelfReportProposer(
        provider,
        model=config.proposer.model,
        temperature=config.proposer.temperature,
    )


def _build_separate_analyzer(
    config: EvolutionConfig,
    *,
    provider: CompletionProvider,
) -> AdaptationProposer:
    from synthorg.engine.evolution.proposers.separate_analyzer import (  # noqa: PLC0415
        SeparateAnalyzerProposer,
    )

    return SeparateAnalyzerProposer(
        provider,
        model=config.proposer.model,
        temperature=config.proposer.temperature,
        max_tokens=config.proposer.max_tokens,
    )


def _build_composite_proposer(
    config: EvolutionConfig,
    *,
    provider: CompletionProvider,
) -> AdaptationProposer:
    from synthorg.engine.evolution.proposers.composite import (  # noqa: PLC0415
        CompositeProposer,
    )

    return CompositeProposer(
        failure_proposer=_build_separate_analyzer(config, provider=provider),
        success_proposer=_build_self_report(config, provider=provider),
    )


_PROPOSER_REGISTRY: StrategyRegistry[AdaptationProposer] = StrategyRegistry(
    {
        "self_report": _build_self_report,
        "separate_analyzer": _build_separate_analyzer,
        "composite": _build_composite_proposer,
    },
    kind="proposer",
)


def _build_proposer(
    config: EvolutionConfig,
    *,
    provider: CompletionProvider | None,
) -> AdaptationProposer:
    """Build proposer from config; falls back to no-op when no provider."""
    if provider is None:
        return _NoOpProposer()
    return _PROPOSER_REGISTRY.build(config.proposer.type, config, provider=provider)


def _build_guard(
    config: EvolutionConfig,
    *,
    identity_store: IdentityVersionStore,
    shadow_runner: ShadowAgentRunner | None,
    shadow_task_sampler: TaskSampler | None,
) -> AdaptationGuard:
    """Build guard chain from config."""
    guards: list[AdaptationGuard] = []
    guard_cfg = config.guards

    if guard_cfg.rate_limit:
        from synthorg.engine.evolution.guards.rate_limit import (  # noqa: PLC0415
            RateLimitGuard,
        )

        guards.append(RateLimitGuard(max_per_day=guard_cfg.rate_limit_per_day))

    if guard_cfg.review_gate:
        from synthorg.engine.evolution.guards.review_gate import (  # noqa: PLC0415
            ReviewGateGuard,
        )

        guards.append(ReviewGateGuard())

    if guard_cfg.rollback:
        from synthorg.engine.evolution.guards.rollback import (  # noqa: PLC0415
            RollbackGuard,
        )

        guards.append(
            RollbackGuard(
                window_tasks=guard_cfg.rollback_window_tasks,
                regression_threshold=guard_cfg.rollback_regression_threshold,
            )
        )

    if guard_cfg.shadow_evaluation is not None:
        guards.append(
            _build_shadow_guard(
                config=guard_cfg.shadow_evaluation,
                identity_store=identity_store,
                shadow_runner=shadow_runner,
                shadow_task_sampler=shadow_task_sampler,
            )
        )

    if not guards:
        from synthorg.engine.evolution.guards.approve_all import (  # noqa: PLC0415
            ApproveAllGuard,
        )

        return ApproveAllGuard()

    if len(guards) == 1:
        return guards[0]

    from synthorg.engine.evolution.guards.composite import (  # noqa: PLC0415
        CompositeGuard,
    )

    return CompositeGuard(guards=tuple(guards))


def _build_shadow_guard(
    *,
    config: ShadowEvaluationConfig,
    identity_store: IdentityVersionStore,
    shadow_runner: ShadowAgentRunner | None,
    shadow_task_sampler: TaskSampler | None,
) -> AdaptationGuard:
    """Wire a real ShadowEvaluationGuard; raise if dependencies are missing."""
    from synthorg.observability.events.evolution import (  # noqa: PLC0415
        EVOLUTION_SHADOW_MISCONFIGURED,
    )

    if shadow_runner is None:
        msg = (
            "shadow_evaluation is enabled but shadow_runner was not "
            "provided to build_evolution_service()"
        )
        logger.error(
            EVOLUTION_SHADOW_MISCONFIGURED,
            missing="shadow_runner",
            task_provider=config.task_provider,
            evaluator_agent_id=config.evaluator_agent_id,
        )
        raise ValueError(msg)

    from synthorg.engine.evolution.guards.shadow_evaluation import (  # noqa: PLC0415
        ShadowEvaluationGuard,
    )
    from synthorg.engine.evolution.guards.shadow_providers import (  # noqa: PLC0415
        ConfiguredShadowTaskProvider,
        RecentTaskHistoryProvider,
    )

    task_provider: ShadowTaskProvider
    if config.task_provider == "configured":
        task_provider = ConfiguredShadowTaskProvider(config=config)
    elif config.task_provider == "recent_history":
        if shadow_task_sampler is None:
            msg = (
                "shadow_evaluation.task_provider='recent_history' but "
                "shadow_task_sampler was not provided to "
                "build_evolution_service()"
            )
            logger.error(
                EVOLUTION_SHADOW_MISCONFIGURED,
                missing="shadow_task_sampler",
                task_provider=config.task_provider,
                evaluator_agent_id=config.evaluator_agent_id,
            )
            raise ValueError(msg)
        task_provider = RecentTaskHistoryProvider(sampler=shadow_task_sampler)
    else:
        # Reachable only if Literal is tampered (model_copy).
        msg = (  # type: ignore[unreachable]
            f"Unknown shadow task provider {config.task_provider!r}; "
            "expected 'configured' or 'recent_history'"
        )
        logger.error(
            EVOLUTION_SHADOW_MISCONFIGURED,
            reason="unknown_task_provider",
            task_provider=config.task_provider,
            evaluator_agent_id=config.evaluator_agent_id,
        )
        raise ValueError(msg)

    return ShadowEvaluationGuard(
        config=config,
        task_provider=task_provider,
        runner=shadow_runner,
        identity_store=identity_store,
    )


def _build_adapters(
    config: EvolutionConfig,
    *,
    identity_store: IdentityVersionStore,
    memory_backend: MemoryBackend | None,
) -> dict[AdaptationAxis, AdaptationAdapter]:
    """Build adapters from config."""
    adapters: dict[AdaptationAxis, AdaptationAdapter] = {}
    adapter_cfg = config.adapters

    if adapter_cfg.identity:
        from synthorg.engine.evolution.adapters.identity_adapter import (  # noqa: PLC0415
            IdentityAdapter,
        )

        adapters[AdaptationAxis.IDENTITY] = IdentityAdapter(
            identity_store=identity_store,
        )

    if adapter_cfg.strategy_selection and memory_backend is not None:
        from synthorg.engine.evolution.adapters.strategy_selection import (  # noqa: PLC0415
            StrategySelectionAdapter,
        )

        adapters[AdaptationAxis.STRATEGY_SELECTION] = StrategySelectionAdapter(
            memory_backend=memory_backend
        )

    if adapter_cfg.prompt_template and memory_backend is not None:
        from synthorg.engine.evolution.adapters.prompt_template import (  # noqa: PLC0415
            PromptTemplateAdapter,
        )

        adapters[AdaptationAxis.PROMPT_TEMPLATE] = PromptTemplateAdapter(
            memory_backend=memory_backend
        )

    return adapters


class _NoOpProposer:
    """Proposer returning no proposals when no LLM provider is available."""

    @property
    def name(self) -> str:
        return "noop"

    async def propose(
        self,
        *,
        agent_id: str,  # noqa: ARG002
        context: object,  # noqa: ARG002
    ) -> tuple[AdaptationProposal, ...]:
        """Return empty -- no LLM provider to generate proposals."""
        return ()
