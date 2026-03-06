"""Routing event constants."""

from typing import Final

ROUTING_ROUTER_BUILT: Final[str] = "routing.router.built"
ROUTING_RESOLVER_BUILT: Final[str] = "routing.resolver.built"
ROUTING_MODEL_RESOLVED: Final[str] = "routing.model.resolved"
ROUTING_MODEL_RESOLUTION_FAILED: Final[str] = "routing.model.resolution_failed"
ROUTING_DECISION_MADE: Final[str] = "routing.decision.made"
ROUTING_FALLBACK_ATTEMPTED: Final[str] = "routing.fallback.attempted"
ROUTING_FALLBACK_EXHAUSTED: Final[str] = "routing.fallback.exhausted"
ROUTING_NO_RULE_MATCHED: Final[str] = "routing.rule.no_match"
ROUTING_BUDGET_EXCEEDED: Final[str] = "routing.budget.exceeded"
ROUTING_SELECTION_FAILED: Final[str] = "routing.selection.failed"
ROUTING_STRATEGY_UNKNOWN: Final[str] = "routing.strategy.unknown"
