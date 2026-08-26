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
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.engine.decomposition.strategy_deps import DecompositionStrategyDeps
from synthorg.engine.errors import DecompositionError
from synthorg.observability import get_logger
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_FAILED,
)
from synthorg.providers.protocol import CompletionProvider
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


def _build_llm_strategy(
    *,
    provider: CompletionProvider,
    decomposition_model: str,
    deps: DecompositionStrategyDeps,
) -> DecompositionStrategy:
    """Build the single-shot LLM decomposition strategy.

    Returns:
        An :class:`LlmDecompositionStrategy` over *provider* + *model*.
    """
    return _llm_strategy(
        provider=provider,
        decomposition_model=decomposition_model,
        cost_tracker=deps.cost_tracker,
        config_resolver=deps.config_resolver,
    )


def _llm_strategy(
    *,
    provider: CompletionProvider,
    decomposition_model: str,
    cost_tracker: CostTrackerProtocol | None,
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
        cost_tracker=cost_tracker,
        config_resolver=config_resolver,
    )


def _build_agent_session_strategy(
    *,
    provider: CompletionProvider,
    decomposition_model: str,
    deps: DecompositionStrategyDeps,
) -> DecompositionStrategy:
    """Build the owner-run agent-session strategy over an LLM fallback.

    Returns:
        An :class:`AgentSessionDecompositionStrategy` whose fallback is the
        single-shot LLM strategy over the same *provider* + *model*.

    Raises:
        ValueError: If *deps* carries no ``provider_selector``. The session
            dispatches each owner on its own bound pair and has no shared
            default to fall back on, so a missing selector is a wiring fault
            rather than a degraded mode.
    """
    from synthorg.engine.decomposition.agent_session import (  # noqa: PLC0415
        AgentSessionDecompositionStrategy,
    )

    return AgentSessionDecompositionStrategy(
        provider_selector=deps.require_provider_selector(),
        fallback=_llm_strategy(
            provider=provider,
            decomposition_model=decomposition_model,
            cost_tracker=deps.cost_tracker,
            config_resolver=deps.config_resolver,
        ),
        deps=deps,
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


def build_decomposition_strategy(
    provider: CompletionProvider | None,
    decomposition_model: str | None,
    *,
    strategy_name: str,
    deps: DecompositionStrategyDeps,
) -> DecompositionStrategy:
    """Select the decomposition strategy from config and available deps.

    Returns:
        The named strategy (agent-session or llm) when both provider deps are
        wired; the no-provider placeholder when neither is.

    Raises:
        ValueError: If exactly one of *provider* / *decomposition_model*
            is supplied -- both or neither must be given; or a provider is
            given without a ``provider_selector`` (the agent session dispatches
            each owner on its own bound provider).
        StrategyFactoryNotFoundError: If *strategy_name* is unknown.
    """
    if provider is not None and decomposition_model is not None:
        if strategy_name == "agent-session":
            # Refused here as well as in the builder, because the registry
            # builds by name and both doors are reachable. One owner answers
            # for both.
            deps.require_provider_selector()
        return _DECOMPOSITION_STRATEGY_REGISTRY.build(
            strategy_name,
            provider=provider,
            decomposition_model=decomposition_model,
            deps=deps,
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
