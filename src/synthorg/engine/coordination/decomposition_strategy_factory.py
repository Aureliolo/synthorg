"""Decomposition-strategy construction for the coordinator factory.

Selects and builds the :class:`DecompositionStrategy` the coordinator
decomposes with: the owner-run agent session (default), the single-shot LLM
strategy, or a placeholder that fails loudly when no provider is wired. Kept
separate from :mod:`synthorg.engine.coordination.factory` so the coordinator
assembly and the strategy-selection logic each stay within their size budget.
"""

from typing import override

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.registry import StrategyRegistry
from synthorg.core.task import Task
from synthorg.engine.decomposition.agent_session import (
    AgentSessionDecompositionConfig,
)
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.engine.decomposition.tool_provider import DecompositionToolProvider
from synthorg.engine.errors import DecompositionError
from synthorg.engine.loop_protocol import ShutdownChecker
from synthorg.memory.injection import MemoryInjectionStrategy
from synthorg.observability import get_logger
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_FAILED,
)
from synthorg.providers.protocol import CompletionProvider, ProviderSelector
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)


class _NoProviderDecompositionStrategy(DecompositionStrategy):
    """Placeholder strategy that raises when no LLM provider is available.

    Used when the factory is called without a provider, so that the
    coordinator can still be constructed (e.g. for manual decomposition
    tests). Attempting to actually decompose will raise a clear error.
    """

    @override
    def get_strategy_name(self) -> str:
        """Return placeholder strategy name."""
        return "no-provider-placeholder"

    @override
    def plans_any_task(self) -> bool:
        """Answer for a strategy that plans nothing at all.

        Returns:
            ``False``: it refuses every task, so recursing into it would only
            convert one refusal into two.
        """
        return False

    @override
    async def decompose(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionPlan:
        """Raise DecompositionError -- no provider configured.

        Raises:
            DecompositionError: Always; this placeholder exists so
                the coordinator can be constructed without a
                provider, but attempting to decompose must fail with
                a clear error.
        """
        msg = (
            "No LLM provider configured for decomposition. "
            "Provide a CompletionProvider and decomposition_model "
            "to enable LLM-based task decomposition."
        )
        logger.warning(
            DECOMPOSITION_FAILED,
            note="Decomposition attempted without LLM provider",
        )
        raise DecompositionError(msg)


def _build_llm_strategy(  # noqa: PLR0913 -- uniform strategy-registry kwargs
    *,
    provider: CompletionProvider,
    decomposition_model: str,
    provider_selector: ProviderSelector | None = None,
    tool_provider: DecompositionToolProvider | None = None,
    cost_tracker: CostTrackerProtocol | None = None,
    shutdown_checker: ShutdownChecker | None = None,
    agent_session_config: AgentSessionDecompositionConfig | None = None,
    planning_memory: MemoryInjectionStrategy | None = None,
    config_resolver: ConfigResolverProtocol | None = None,
) -> DecompositionStrategy:
    """Build the single-shot LLM decomposition strategy.

    The agent-session-only deps (*provider_selector*, *tool_provider*,
    *cost_tracker*, *shutdown_checker*, *planning_memory*, the session config)
    are accepted so the strategy registry can pass a uniform kwarg set to every
    builder; the single-shot strategy ignores them.

    Returns:
        An :class:`LlmDecompositionStrategy` over *provider* + *model*.
    """
    del provider_selector, tool_provider, cost_tracker, shutdown_checker
    del agent_session_config, planning_memory
    return _llm_strategy(
        provider=provider,
        decomposition_model=decomposition_model,
        config_resolver=config_resolver,
    )


def _llm_strategy(
    *,
    provider: CompletionProvider,
    decomposition_model: str,
    config_resolver: ConfigResolverProtocol | None,
) -> DecompositionStrategy:
    """Build the single-shot LLM strategy over the operator's live settings.

    Shared by both builders, because the agent-session strategy falls back to
    this one: a resolver given to the primary path and not the fallback would
    leave the fallback truncating at a ceiling the operator had already raised.

    Returns:
        An :class:`LlmDecompositionStrategy` over *provider* + *model*.
    """
    from synthorg.engine.decomposition.llm import (  # noqa: PLC0415
        LlmDecompositionStrategy,
    )

    return LlmDecompositionStrategy(
        provider=provider,
        model=decomposition_model,
        config_resolver=config_resolver,
    )


def _build_agent_session_strategy(  # noqa: PLR0913 -- uniform registry kwargs
    *,
    provider: CompletionProvider,
    decomposition_model: str,
    provider_selector: ProviderSelector,
    tool_provider: DecompositionToolProvider | None = None,
    cost_tracker: CostTrackerProtocol | None = None,
    shutdown_checker: ShutdownChecker | None = None,
    agent_session_config: AgentSessionDecompositionConfig | None = None,
    planning_memory: MemoryInjectionStrategy | None = None,
    config_resolver: ConfigResolverProtocol | None = None,
) -> DecompositionStrategy:
    """Build the owner-run agent-session strategy over an LLM fallback.

    The session's turn cap, spend bounds and memory-digest budget arrive as one
    config rather than as loose scalars, for the reason the config's own
    ``ceilings`` field gives for being one field instead of two: a wiring path
    that resolves some of them cannot leave the rest at their defaults in
    silence.

    Returns:
        An :class:`AgentSessionDecompositionStrategy` whose fallback is the
        single-shot LLM strategy over the same *provider* + *model*;
        *planning_memory* pre-seeds the org/retro digest into the brief.
    """
    from synthorg.engine.decomposition.agent_session import (  # noqa: PLC0415
        AgentSessionDecompositionStrategy,
    )

    return AgentSessionDecompositionStrategy(
        provider_selector=provider_selector,
        fallback=_llm_strategy(
            provider=provider,
            decomposition_model=decomposition_model,
            config_resolver=config_resolver,
        ),
        tool_provider=tool_provider,
        config=agent_session_config or AgentSessionDecompositionConfig(),
        cost_tracker=cost_tracker,
        shutdown_checker=shutdown_checker,
        planning_memory=planning_memory,
    )


_DECOMPOSITION_STRATEGY_REGISTRY: StrategyRegistry[DecompositionStrategy] = (
    StrategyRegistry(
        {
            "agent-session": _build_agent_session_strategy,
            "llm": _build_llm_strategy,
        },
        kind="decomposition_strategy",
    )
)


def build_decomposition_strategy(  # noqa: PLR0913 -- shared session deps
    provider: CompletionProvider | None,
    decomposition_model: str | None,
    *,
    strategy_name: str,
    tool_provider: DecompositionToolProvider | None,
    provider_selector: ProviderSelector | None = None,
    cost_tracker: CostTrackerProtocol | None = None,
    shutdown_checker: ShutdownChecker | None = None,
    agent_session_config: AgentSessionDecompositionConfig | None = None,
    planning_memory: MemoryInjectionStrategy | None = None,
    config_resolver: ConfigResolverProtocol | None = None,
) -> DecompositionStrategy:
    """Select the decomposition strategy from config and available deps.

    Returns:
        The named strategy (agent-session or llm) when both provider deps are
        wired; the no-provider placeholder when neither is.

    Raises:
        ValueError: If exactly one of *provider* / *decomposition_model*
            is supplied -- both or neither must be given; or a provider is
            given without a *provider_selector* (the agent session dispatches
            each owner on its own bound provider).
        StrategyFactoryNotFoundError: If *strategy_name* is unknown.
    """
    if provider is not None and decomposition_model is not None:
        if strategy_name == "agent-session" and provider_selector is None:
            msg = (
                "The owner-run agent-session decomposition requires a "
                "provider_selector: each owner dispatches on its own bound "
                "(provider, model), never a shared default. The single-shot "
                "'llm' strategy needs no selector."
            )
            raise ValueError(msg)
        return _DECOMPOSITION_STRATEGY_REGISTRY.build(
            strategy_name,
            provider=provider,
            decomposition_model=decomposition_model,
            provider_selector=provider_selector,
            tool_provider=tool_provider,
            cost_tracker=cost_tracker,
            shutdown_checker=shutdown_checker,
            agent_session_config=agent_session_config,
            planning_memory=planning_memory,
            config_resolver=config_resolver,
        )
    if (provider is None) != (decomposition_model is None):
        given = "provider" if provider is not None else "decomposition_model"
        missing = "decomposition_model" if provider is not None else "provider"
        msg = (
            f"Decomposition requires both provider and decomposition_model, "
            f"but only {given} was supplied (missing {missing})"
        )
        logger.warning(
            DECOMPOSITION_FAILED,
            note="Mismatched decomposition dependencies",
            given=given,
            missing=missing,
        )
        raise ValueError(msg)
    return _NoProviderDecompositionStrategy()
