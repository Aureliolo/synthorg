"""Routing strategies -- stateless implementations of ``RoutingStrategy``.

Each strategy selects a model given a ``RoutingRequest``, a
``RoutingConfig``, and a ``ModelResolver``.  Strategies are stateless
singletons registered in a module-level mapping.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, NoReturn, Protocol, runtime_checkable

from synthorg.core.role_catalog import get_seniority_info
from synthorg.hr.seniority import SeniorityLevel
from synthorg.observability import get_logger
from synthorg.observability.events.routing import (
    ROUTING_BUDGET_EXCEEDED,
    ROUTING_FALLBACK_EXHAUSTED,
    ROUTING_NO_RULE_MATCHED,
)

from ._smart_strategy import SmartStrategy
from ._strategy_helpers import (
    _cheapest_within_budget,
    _fastest_within_budget,
    _try_resolve_with_fallback,
    _try_resolve_with_fallback_safe,
    _try_task_type_rules,
    _within_budget,
)
from ._strategy_names import (
    STRATEGY_NAME_CHEAPEST,
    STRATEGY_NAME_COST_AWARE,
    STRATEGY_NAME_FASTEST,
    STRATEGY_NAME_MANUAL,
    STRATEGY_NAME_ROLE_BASED,
    STRATEGY_NAME_SMART,
)
from .errors import ModelResolutionError, NoAvailableModelError
from .models import RoutingDecision, RoutingRequest
from .resolver import ModelResolver

if TYPE_CHECKING:
    from synthorg.config.agent_schema import RoutingConfig

logger = get_logger(__name__)

# ``SmartStrategy`` and the ``STRATEGY_NAME_*`` constants are defined in
# sibling modules and re-exported here so the canonical public path
# ``providers.routing.strategies`` stays stable under ``no_implicit_reexport``.
__all__ = [
    "STRATEGY_MAP",
    "STRATEGY_NAME_CHEAPEST",
    "STRATEGY_NAME_COST_AWARE",
    "STRATEGY_NAME_FASTEST",
    "STRATEGY_NAME_MANUAL",
    "STRATEGY_NAME_ROLE_BASED",
    "STRATEGY_NAME_SMART",
    "CostAwareStrategy",
    "FastestStrategy",
    "ManualStrategy",
    "RoleBasedStrategy",
    "RoutingStrategy",
    "SmartStrategy",
]


# ── Protocol ──────────────────────────────────────────────────────


@runtime_checkable
class RoutingStrategy(Protocol):
    """Protocol for model routing strategies."""

    @property
    def name(self) -> str:
        """Strategy name (matches config value)."""
        ...

    def select(
        self,
        request: RoutingRequest,
        config: RoutingConfig,
        resolver: ModelResolver,
    ) -> RoutingDecision:
        """Select a model for the given request.

        Args:
            request: Routing inputs (agent level, task type, etc.).
            config: Routing configuration (rules, fallback chain).
            resolver: Model resolver for alias/ID lookup.

        Returns:
            A routing decision with the chosen model.

        Raises:
            ModelResolutionError: If the requested model cannot be found.
            NoAvailableModelError: If all candidates are exhausted.
        """
        ...


# ── Strategy 1: Manual ────────────────────────────────────────────


class ManualStrategy:
    """Resolve an explicit model override.

    Requires ``request.model_override`` to be set.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return STRATEGY_NAME_MANUAL

    def select(
        self,
        request: RoutingRequest,
        config: RoutingConfig,  # noqa: ARG002
        resolver: ModelResolver,
    ) -> RoutingDecision:
        """Select the explicitly requested model.

        Returns:
            A ``RoutingDecision`` resolving the explicit
            ``model_override``.

        Raises:
            ModelResolutionError: If ``model_override`` is not set or
                the model cannot be resolved.
        """
        if request.model_override is None:
            logger.warning(
                ROUTING_NO_RULE_MATCHED,
                strategy=self.name,
                reason="model_override not set",
            )
            msg = "ManualStrategy requires model_override to be set"
            raise ModelResolutionError(msg)

        model = resolver.resolve(request.model_override)
        return RoutingDecision(
            resolved_model=model,
            strategy_used=self.name,
            reason=f"Explicit override: {request.model_override}",
        )


# ── Strategy 2: Role-Based ───────────────────────────────────────


class RoleBasedStrategy:
    """Select model based on agent seniority level.

    Matches the first routing rule where ``rule.role_level`` equals
    ``request.agent_level``.  If no rule matches, uses the seniority
    catalog's ``typical_model_tier`` as a fallback lookup.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return STRATEGY_NAME_ROLE_BASED

    def select(
        self,
        request: RoutingRequest,
        config: RoutingConfig,
        resolver: ModelResolver,
    ) -> RoutingDecision:
        """Select model based on role level.

        Returns:
            A ``RoutingDecision`` from role rules, the seniority catalog
            default, or the fallback chain.

        Raises:
            ModelResolutionError: If no agent_level is set.
            NoAvailableModelError: If all candidates are exhausted.
        """
        level = self._require_level(request)
        return (
            self._try_rule_match(level, config, resolver)
            or self._try_seniority(level, config, resolver)
            or self._raise_no_available(level, config)
        )

    def _require_level(
        self,
        request: RoutingRequest,
    ) -> SeniorityLevel:
        """Validate that agent_level is set.

        Returns:
            The ``SeniorityLevel`` from the request.

        Raises:
            ModelResolutionError: If ``request.agent_level`` is ``None``.
        """
        if request.agent_level is None:
            logger.warning(
                ROUTING_NO_RULE_MATCHED,
                strategy=self.name,
                reason="agent_level not set",
            )
            msg = "RoleBasedStrategy requires agent_level to be set"
            raise ModelResolutionError(msg)
        return request.agent_level

    def _try_rule_match(
        self,
        level: SeniorityLevel,
        config: RoutingConfig,
        resolver: ModelResolver,
    ) -> RoutingDecision | None:
        """Match routing rules by role level.

        Returns:
            A ``RoutingDecision`` for the first matching role-level rule
            that resolves, or ``None`` if none match or resolve.
        """
        for rule in config.rules:
            if rule.role_level == level:
                model, tried = _try_resolve_with_fallback(
                    rule.preferred_model,
                    rule,
                    config,
                    resolver,
                )
                return RoutingDecision(
                    resolved_model=model,
                    strategy_used=self.name,
                    reason=(
                        f"Role rule match: level={level.value}, model={model.model_id}"
                    ),
                    fallbacks_tried=tried,
                )
        logger.debug(
            ROUTING_NO_RULE_MATCHED,
            level=level.value,
            strategy=self.name,
        )
        return None

    def _try_seniority(
        self,
        level: SeniorityLevel,
        config: RoutingConfig,
        resolver: ModelResolver,
    ) -> RoutingDecision | None:
        """Fall back to seniority catalog default tier.

        Returns:
            A ``RoutingDecision`` from the seniority catalog's typical
            tier if it resolves, or ``None`` otherwise.
        """
        try:
            tier = get_seniority_info(level).typical_model_tier
        except LookupError:
            logger.warning(
                ROUTING_NO_RULE_MATCHED,
                level=level.value,
                strategy=self.name,
                reason="seniority level not in catalog",
            )
            return None
        result = _try_resolve_with_fallback_safe(
            tier,
            None,
            config,
            resolver,
        )
        if result is None:
            return None
        model, tried = result
        return RoutingDecision(
            resolved_model=model,
            strategy_used=self.name,
            reason=f"Seniority default: level={level.value}, tier={tier}",
            fallbacks_tried=tried,
        )

    def _raise_no_available(
        self,
        level: SeniorityLevel,
        config: RoutingConfig,
    ) -> NoReturn:
        """Raise when all candidates are exhausted.

        Raises:
            NoAvailableModelError: Always; signals that no model could be
                resolved for the request.
        """
        tier: str = "unknown"
        try:
            tier = get_seniority_info(level).typical_model_tier
        except LookupError:
            logger.debug(
                ROUTING_NO_RULE_MATCHED,
                level=level.value,
                reason="seniority level not in catalog",
            )
        if config.fallback_chain:
            chain_detail = f"fallback chain exhausted: {list(config.fallback_chain)}"
        else:
            chain_detail = "no fallback chain configured"
        msg = (
            f"No model available for level={level.value} "
            f"(tier={tier}, no rules matched, {chain_detail})"
        )
        logger.warning(
            ROUTING_FALLBACK_EXHAUSTED,
            level=level.value,
            tier=tier,
            strategy=self.name,
        )
        raise NoAvailableModelError(msg)


# ── Strategy 3: Cost-Aware ───────────────────────────────────────


class CostAwareStrategy:
    """Select the cheapest model, optionally respecting a budget.

    Matches ``task_type`` rules first, then falls back to the cheapest
    model from the resolver.
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return STRATEGY_NAME_COST_AWARE

    def select(
        self,
        request: RoutingRequest,
        config: RoutingConfig,
        resolver: ModelResolver,
    ) -> RoutingDecision:
        """Select the cheapest available model.

        Returns:
            A ``RoutingDecision`` for the cheapest model within budget
            (or the cheapest overall if all exceed budget).

        Raises:
            NoAvailableModelError: If no models are registered.
        """
        decision = _try_task_type_rules(
            request,
            config,
            resolver,
            self.name,
        )
        if decision is not None:
            if _within_budget(decision.resolved_model, request.remaining_budget):
                return decision
            logger.info(
                ROUTING_BUDGET_EXCEEDED,
                model=decision.resolved_model.model_id,
                cost=decision.resolved_model.total_cost_per_1k,
                remaining_budget=request.remaining_budget,
                source="task_type_rule_budget_check",
                strategy=self.name,
            )

        # Pick cheapest
        model, budget_exceeded = _cheapest_within_budget(
            resolver,
            request.remaining_budget,
        )
        reason = f"Cheapest available: {model.model_id}"
        if budget_exceeded:
            reason += " (all models exceed remaining budget)"
        return RoutingDecision(
            resolved_model=model,
            strategy_used=self.name,
            reason=reason,
        )


# ── Strategy 4: Fastest ──────────────────────────────────────────


class FastestStrategy:
    """Select the fastest model, optionally respecting a budget.

    Matches ``task_type`` rules first, then falls back to the fastest
    model from the resolver.  When no models have latency data,
    delegates to cheapest (lower-cost models are typically smaller
    and faster, making cost a reasonable proxy).
    """

    @property
    def name(self) -> str:
        """Return strategy name."""
        return STRATEGY_NAME_FASTEST

    def select(
        self,
        request: RoutingRequest,
        config: RoutingConfig,
        resolver: ModelResolver,
    ) -> RoutingDecision:
        """Select the fastest available model.

        Returns:
            A ``RoutingDecision`` for the lowest-latency model within
            budget (cheapest if no latency data, or cheapest overall if
            all exceed budget).

        Raises:
            NoAvailableModelError: If no models are registered.
        """
        skipped_task_rule: str | None = None
        decision = _try_task_type_rules(
            request,
            config,
            resolver,
            self.name,
        )
        if decision is not None:
            if _within_budget(decision.resolved_model, request.remaining_budget):
                return decision
            skipped_task_rule = decision.resolved_model.model_id
            logger.info(
                ROUTING_BUDGET_EXCEEDED,
                model=decision.resolved_model.model_id,
                cost=decision.resolved_model.total_cost_per_1k,
                remaining_budget=request.remaining_budget,
                source="task_type_rule_budget_check",
                strategy=self.name,
            )

        # Pick fastest
        model, budget_exceeded = _fastest_within_budget(
            resolver,
            request.remaining_budget,
        )
        # _fastest_within_budget may delegate to cheapest when no latency data
        basis = (
            "fastest"
            if model.estimated_latency_ms is not None
            else "cheapest (no latency data)"
        )
        reason = f"Selected by {basis}: {model.model_id}"
        if budget_exceeded:
            reason += " (all models exceed remaining budget)"
        if skipped_task_rule is not None:
            reason += f" (task-type rule {skipped_task_rule!r} exceeded budget)"
        return RoutingDecision(
            resolved_model=model,
            strategy_used=self.name,
            reason=reason,
        )


# ── Strategy Registry ────────────────────────────────────────────

_MANUAL = ManualStrategy()
_ROLE_BASED = RoleBasedStrategy()
_COST_AWARE = CostAwareStrategy()
_FASTEST = FastestStrategy()
_SMART = SmartStrategy()

STRATEGY_MAP: Mapping[str, RoutingStrategy] = MappingProxyType(
    {
        STRATEGY_NAME_MANUAL: _MANUAL,
        STRATEGY_NAME_ROLE_BASED: _ROLE_BASED,
        STRATEGY_NAME_COST_AWARE: _COST_AWARE,
        STRATEGY_NAME_FASTEST: _FASTEST,
        STRATEGY_NAME_SMART: _SMART,
        STRATEGY_NAME_CHEAPEST: _COST_AWARE,  # Alias for cost_aware
    },
)
"""Maps config strategy names to singleton instances."""

assert all(isinstance(s, RoutingStrategy) for s in STRATEGY_MAP.values()), (  # noqa: S101
    "All STRATEGY_MAP entries must satisfy RoutingStrategy"
)
