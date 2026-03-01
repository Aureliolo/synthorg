"""Model router — main entry point for routing decisions.

Constructed from ``RoutingConfig`` and a provider config dict.
Delegates to strategy implementations.
"""

from typing import TYPE_CHECKING

from ai_company.observability import get_logger
from ai_company.observability.events import (
    ROUTING_DECISION_MADE,
    ROUTING_ROUTER_BUILT,
    ROUTING_SELECTION_FAILED,
    ROUTING_STRATEGY_UNKNOWN,
)

from .errors import RoutingError, UnknownStrategyError
from .resolver import ModelResolver
from .strategies import STRATEGY_MAP

logger = get_logger(__name__)

if TYPE_CHECKING:
    from ai_company.config.schema import ProviderConfig, RoutingConfig

    from .models import RoutingDecision, RoutingRequest


class ModelRouter:
    """Route requests to the appropriate LLM model.

    Examples:
        Build from config::

            router = ModelRouter(
                routing_config=root_config.routing,
                providers=root_config.providers,
            )
            decision = router.route(
                RoutingRequest(agent_level=SeniorityLevel.SENIOR),
            )
    """

    def __init__(
        self,
        routing_config: RoutingConfig,
        providers: dict[str, ProviderConfig],
    ) -> None:
        """Initialize the router.

        Args:
            routing_config: Routing configuration (strategy, rules, fallback).
            providers: Provider configurations keyed by provider name.

        Raises:
            UnknownStrategyError: If the configured strategy is not recognized.
        """
        self._config = routing_config
        self._resolver = ModelResolver.from_config(providers)

        strategy_name = routing_config.strategy
        strategy = STRATEGY_MAP.get(strategy_name)
        if strategy is None:
            logger.error(
                ROUTING_STRATEGY_UNKNOWN,
                strategy=strategy_name,
                available=sorted(STRATEGY_MAP),
            )
            msg = (
                f"Unknown routing strategy {strategy_name!r}. "
                f"Available: {sorted(STRATEGY_MAP)}"
            )
            raise UnknownStrategyError(
                msg,
                context={"strategy": strategy_name},
            )
        self._strategy = strategy

        logger.info(
            ROUTING_ROUTER_BUILT,
            strategy_configured=strategy_name,
            strategy=self._strategy.name,
            rule_count=len(routing_config.rules),
            fallback_count=len(routing_config.fallback_chain),
        )

    @property
    def resolver(self) -> ModelResolver:
        """Return the underlying model resolver."""
        return self._resolver

    @property
    def strategy_name(self) -> str:
        """Return the active strategy name."""
        return self._strategy.name

    def route(self, request: RoutingRequest) -> RoutingDecision:
        """Route a request to a model.

        Args:
            request: Routing inputs.

        Returns:
            A routing decision with the chosen model.

        Raises:
            ModelResolutionError: If a required model cannot be found.
            NoAvailableModelError: If all candidates are exhausted.
        """
        try:
            decision = self._strategy.select(
                request,
                self._config,
                self._resolver,
            )
        except RoutingError:
            logger.warning(
                ROUTING_SELECTION_FAILED,
                strategy=self._strategy.name,
                agent_level=(
                    request.agent_level.value
                    if request.agent_level is not None
                    else None
                ),
                task_type=request.task_type,
                model_override=request.model_override,
            )
            raise
        logger.info(
            ROUTING_DECISION_MADE,
            strategy=decision.strategy_used,
            provider=decision.resolved_model.provider_name,
            model=decision.resolved_model.model_id,
            reason=decision.reason,
            fallbacks_tried=decision.fallbacks_tried,
        )
        return decision
