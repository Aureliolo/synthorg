"""Model routing engine -- strategy-based LLM model selection.

Exports the router, resolver, domain models, errors, strategies,
and the ``RoutingStrategy`` protocol.
"""

from .errors import (
    ModelResolutionError,
    NoAvailableModelError,
    RoutingError,
    UnknownRoutingStrategyError,
)
from .models import ResolvedModel, RoutingDecision, RoutingRequest
from .resolver import ModelResolver
from .router import ModelRouter
from .selector import (
    CheapestSelector,
    ModelCandidateSelector,
    QuotaAwareSelector,
)
from .strategies import (
    STRATEGY_MAP,
    STRATEGY_NAME_CHEAPEST,
    STRATEGY_NAME_COST_AWARE,
    STRATEGY_NAME_FASTEST,
    STRATEGY_NAME_MANUAL,
    STRATEGY_NAME_ROLE_BASED,
    STRATEGY_NAME_SMART,
    CostAwareStrategy,
    FastestStrategy,
    ManualStrategy,
    RoleBasedStrategy,
    RoutingStrategy,
    SmartStrategy,
)

__all__ = [
    "STRATEGY_MAP",
    "STRATEGY_NAME_CHEAPEST",
    "STRATEGY_NAME_COST_AWARE",
    "STRATEGY_NAME_FASTEST",
    "STRATEGY_NAME_MANUAL",
    "STRATEGY_NAME_ROLE_BASED",
    "STRATEGY_NAME_SMART",
    "CheapestSelector",
    "CostAwareStrategy",
    "FastestStrategy",
    "ManualStrategy",
    "ModelCandidateSelector",
    "ModelResolutionError",
    "ModelResolver",
    "ModelRouter",
    "NoAvailableModelError",
    "QuotaAwareSelector",
    "ResolvedModel",
    "RoleBasedStrategy",
    "RoutingDecision",
    "RoutingError",
    "RoutingRequest",
    "RoutingStrategy",
    "SmartStrategy",
    "UnknownRoutingStrategyError",
]
