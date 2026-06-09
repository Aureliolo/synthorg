"""SmartStrategy -- priority-based signal merging for model routing.

Merges override / task-type / role / seniority / cheapest / global-chain
signals in priority order.
"""

from typing import TYPE_CHECKING, NoReturn

from synthorg.observability import get_logger
from synthorg.observability.events.routing import (
    ROUTING_FALLBACK_EXHAUSTED,
    ROUTING_MODEL_RESOLUTION_FAILED,
)

from ._strategy_helpers import (
    _cheapest_within_budget,
    _try_role_rules,
    _try_seniority_default,
    _try_task_type_rules,
    _walk_fallback_chain,
)
from ._strategy_names import STRATEGY_NAME_SMART
from .errors import NoAvailableModelError
from .models import RoutingDecision, RoutingRequest
from .resolver import ModelResolver

if TYPE_CHECKING:
    from synthorg.config.agent_schema import RoutingConfig

logger = get_logger(__name__)


class SmartStrategy:
    """Combined strategy with priority-based signal merging.

    Priority order: model_override > task_type rules > role_level
    rules > seniority default > cheapest available (budget-aware) >
    global fallback_chain > exhausted.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return STRATEGY_NAME_SMART

    def select(
        self,
        request: RoutingRequest,
        config: RoutingConfig,
        resolver: ModelResolver,
    ) -> RoutingDecision:
        """Select a model using all available signals.

        Returns:
            A ``RoutingDecision`` from the highest-priority signal that
            resolves (override > task_type > role > seniority > cheapest
            > global fallback chain).

        Raises:
            NoAvailableModelError: If all candidates are exhausted.
        """
        return (
            self._try_override(request, resolver)
            or _try_task_type_rules(
                request,
                config,
                resolver,
                self.name,
            )
            or _try_role_rules(
                request,
                config,
                resolver,
                self.name,
            )
            or _try_seniority_default(
                request,
                resolver,
                self.name,
            )
            or self._try_cheapest(request, resolver)
            or self._try_global_chain(config, resolver)
            or self._raise_exhausted()
        )

    def _try_override(
        self,
        request: RoutingRequest,
        resolver: ModelResolver,
    ) -> RoutingDecision | None:
        """Attempt to resolve model_override as a soft preference.

        Unlike ``ManualStrategy`` (which raises on unresolvable overrides),
        SmartStrategy treats overrides as best-effort hints -- if the
        override cannot be resolved, the strategy falls through to the
        next signal in the priority chain rather than failing the request.

        Returns:
            A ``RoutingDecision`` resolving ``model_override`` if present
            and resolvable, or ``None`` to fall through to the next
            signal.
        """
        if request.model_override is None:
            return None
        model = resolver.resolve_safe(request.model_override)
        if model is None:
            logger.warning(
                ROUTING_MODEL_RESOLUTION_FAILED,
                ref=request.model_override,
                source="smart_override",
            )
            return None
        return RoutingDecision(
            resolved_model=model,
            strategy_used=self.name,
            reason=f"Explicit override: {request.model_override}",
        )

    def _try_cheapest(
        self,
        request: RoutingRequest,
        resolver: ModelResolver,
    ) -> RoutingDecision | None:
        """Return cheapest model within budget, or None if no models."""
        try:
            model, budget_exceeded = _cheapest_within_budget(
                resolver,
                request.remaining_budget,
            )
        except NoAvailableModelError:
            logger.info(
                ROUTING_FALLBACK_EXHAUSTED,
                source="smart_cheapest_fallback",
                strategy=self.name,
                reason="no models available for cheapest fallback",
            )
            return None
        reason = f"Cheapest available: {model.model_id}"
        if budget_exceeded:
            reason += " (all models exceed remaining budget)"
        return RoutingDecision(
            resolved_model=model,
            strategy_used=self.name,
            reason=reason,
        )

    def _try_global_chain(
        self,
        config: RoutingConfig,
        resolver: ModelResolver,
    ) -> RoutingDecision | None:
        """Resolve a model from the global fallback chain, if any.

        Returns:
            A ``RoutingDecision`` naming the resolved model and the chain
            entries tried, or ``None`` if the chain yields no model.
        """
        chain_result = _walk_fallback_chain(config, resolver)
        if chain_result is None:
            return None
        model, tried = chain_result
        return RoutingDecision(
            resolved_model=model,
            strategy_used=self.name,
            reason="Global fallback chain",
            fallbacks_tried=tried,
        )

    def _raise_exhausted(self) -> NoReturn:
        """Log and raise once every routing signal is exhausted.

        Raises:
            NoAvailableModelError: Always; no model could be resolved
                from any signal.
        """
        logger.warning(
            ROUTING_FALLBACK_EXHAUSTED,
            strategy=STRATEGY_NAME_SMART,
            reason="all signals exhausted",
        )
        msg = "SmartStrategy: no model available from any signal"
        raise NoAvailableModelError(
            msg,
            context={"strategy": STRATEGY_NAME_SMART},
        )
