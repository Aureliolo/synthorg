"""Private helper functions shared by routing strategy implementations.

Extracted from ``strategies.py`` to keep strategy classes focused on
selection logic.
"""

from typing import TYPE_CHECKING

from synthorg.core.role_catalog import get_seniority_info
from synthorg.observability import get_logger
from synthorg.observability.events.routing import (
    ROUTING_BUDGET_EXCEEDED,
    ROUTING_FALLBACK_ATTEMPTED,
    ROUTING_FALLBACK_EXHAUSTED,
    ROUTING_NO_RULE_MATCHED,
)

from .errors import NoAvailableModelError
from .models import ResolvedModel, RoutingDecision, RoutingRequest
from .resolver import ModelResolver  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.config.schema import RoutingConfig, RoutingRuleConfig

logger = get_logger(__name__)


def _try_candidate(
    ref: str,
    source: str,
    resolver: ModelResolver,
    seen: set[str],
    tried: list[str],
) -> ResolvedModel | None:
    """Try to resolve a single candidate ref, recording failure.

    Skips refs already in *seen*. On miss, appends to *tried*/*seen*
    and logs at DEBUG.
    """
    if ref in seen:
        return None
    model = resolver.resolve_safe(ref)
    if model is not None:
        return model
    tried.append(ref)
    seen.add(ref)
    logger.debug(ROUTING_FALLBACK_ATTEMPTED, ref=ref, source=source)
    return None


def _try_resolve_with_fallback(
    ref: str,
    rule: RoutingRuleConfig | None,
    config: RoutingConfig,
    resolver: ModelResolver,
) -> tuple[ResolvedModel, tuple[str, ...]]:
    """Try to resolve *ref*, then rule fallback, then global chain.

    Returns:
        Tuple of (resolved_model, fallbacks_tried).

    Raises:
        NoAvailableModelError: If all candidates are exhausted.
    """
    tried: list[str] = []
    seen: set[str] = set()

    model = _try_candidate(ref, "primary", resolver, seen, tried)
    if model is not None:
        return model, tuple(tried)

    if rule is not None and rule.fallback is not None:
        model = _try_candidate(
            rule.fallback,
            "rule_fallback",
            resolver,
            seen,
            tried,
        )
        if model is not None:
            return model, tuple(tried)

    for chain_ref in config.fallback_chain:
        model = _try_candidate(
            chain_ref,
            "global_chain",
            resolver,
            seen,
            tried,
        )
        if model is not None:
            return model, tuple(tried)

    logger.warning(ROUTING_FALLBACK_EXHAUSTED, tried=tried)
    msg = f"All model candidates exhausted: {tried}"
    raise NoAvailableModelError(msg, context={"tried": tried})


def _try_resolve_with_fallback_safe(
    ref: str,
    rule: RoutingRuleConfig | None,
    config: RoutingConfig,
    resolver: ModelResolver,
) -> tuple[ResolvedModel, tuple[str, ...]] | None:
    """Like ``_try_resolve_with_fallback`` but returns ``None``.

    Returns ``None`` instead of raising ``NoAvailableModelError``.
    """
    try:
        return _try_resolve_with_fallback(ref, rule, config, resolver)
    except NoAvailableModelError:
        return None


def _walk_fallback_chain(
    config: RoutingConfig,
    resolver: ModelResolver,
) -> tuple[ResolvedModel, tuple[str, ...]] | None:
    """Walk the global fallback chain, return first resolvable.

    Returns:
        Tuple of ``(resolved_model, fallbacks_tried)`` if any model
        resolves, or ``None`` if the entire chain is exhausted.
    """
    if not config.fallback_chain:
        logger.debug(
            ROUTING_FALLBACK_EXHAUSTED,
            source="global_chain",
            reason="no fallback chain configured",
        )
        return None

    tried: list[str] = []
    for ref in config.fallback_chain:
        model = resolver.resolve_safe(ref)
        if model is not None:
            return model, tuple(tried)
        tried.append(ref)
    logger.warning(
        ROUTING_FALLBACK_EXHAUSTED,
        tried=tried,
        source="global_chain",
    )
    return None


def _within_budget(
    model: ResolvedModel,
    remaining_budget: float | None,
) -> bool:
    """Check whether a model's cost is within the remaining budget."""
    if remaining_budget is None:
        return True
    return model.total_cost_per_1k <= remaining_budget


def _cheapest_within_budget(
    resolver: ModelResolver,
    remaining_budget: float | None,
) -> tuple[ResolvedModel, bool]:
    """Pick the cheapest model within budget, or the absolute cheapest.

    Returns:
        Tuple of (model, budget_exceeded).  If budget is exceeded by
        all models, returns cheapest anyway with ``budget_exceeded=True``.

    Raises:
        NoAvailableModelError: If no models are registered at all.
    """
    all_models = resolver.all_models_sorted_by_cost()
    if not all_models:
        logger.warning(
            ROUTING_FALLBACK_EXHAUSTED,
            source="cheapest_within_budget",
            reason="no models registered",
        )
        msg = "No models registered in resolver"
        raise NoAvailableModelError(msg)

    if remaining_budget is None:
        return all_models[0], False

    for model in all_models:
        if model.total_cost_per_1k <= remaining_budget:
            return model, False

    cheapest = all_models[0]
    cheapest_cost = cheapest.total_cost_per_1k
    logger.warning(
        ROUTING_BUDGET_EXCEEDED,
        remaining_budget=remaining_budget,
        model_cost=cheapest_cost,
    )
    return cheapest, True


def _fastest_within_budget(
    resolver: ModelResolver,
    remaining_budget: float | None,
) -> tuple[ResolvedModel, bool]:
    """Pick the fastest model within budget, falling back to cheapest.

    Returns:
        Tuple of ``(model, budget_exceeded)``.

    Raises:
        NoAvailableModelError: If no models are registered at all.
    """
    all_models = resolver.all_models_sorted_by_latency()
    if not all_models:
        logger.warning(
            ROUTING_FALLBACK_EXHAUSTED,
            source="fastest_within_budget",
            reason="no models registered",
        )
        msg = "No models registered in resolver"
        raise NoAvailableModelError(msg)

    # If no model has latency data, fall back to cheapest
    models_with_latency = [m for m in all_models if m.estimated_latency_ms is not None]
    if not models_with_latency:
        logger.info(
            ROUTING_FALLBACK_ATTEMPTED,
            ref=None,
            source="fastest_within_budget",
            reason="no latency data, delegating to cheapest",
        )
        return _cheapest_within_budget(resolver, remaining_budget)

    if remaining_budget is None:
        return models_with_latency[0], False

    for model in models_with_latency:
        if model.total_cost_per_1k <= remaining_budget:
            return model, False

    fastest = models_with_latency[0]
    fastest_cost = fastest.total_cost_per_1k
    logger.warning(
        ROUTING_BUDGET_EXCEEDED,
        remaining_budget=remaining_budget,
        model_cost=fastest_cost,
        model=fastest.model_id,
    )
    return fastest, True


def _try_task_type_rules(
    request: RoutingRequest,
    config: RoutingConfig,
    resolver: ModelResolver,
    strategy_name: str,
) -> RoutingDecision | None:
    """Match task_type rules; return decision or None."""
    if request.task_type is None:
        return None
    for rule in config.rules:
        if rule.task_type == request.task_type:
            result = _try_resolve_with_fallback_safe(
                rule.preferred_model,
                rule,
                config,
                resolver,
            )
            if result is not None:
                model, tried = result
                return RoutingDecision(
                    resolved_model=model,
                    strategy_used=strategy_name,
                    reason=(
                        f"Task-type rule: type={request.task_type}"
                        f", model={model.model_id}"
                    ),
                    fallbacks_tried=tried,
                )
    logger.debug(
        ROUTING_NO_RULE_MATCHED,
        task_type=request.task_type,
        strategy=strategy_name,
        source="task_type_rules",
    )
    return None


def _try_role_rules(
    request: RoutingRequest,
    config: RoutingConfig,
    resolver: ModelResolver,
    strategy_name: str,
) -> RoutingDecision | None:
    """Match role_level rules; return decision or None."""
    if request.agent_level is None:
        return None
    for rule in config.rules:
        if rule.role_level == request.agent_level:
            result = _try_resolve_with_fallback_safe(
                rule.preferred_model,
                rule,
                config,
                resolver,
            )
            if result is not None:
                model, tried = result
                return RoutingDecision(
                    resolved_model=model,
                    strategy_used=strategy_name,
                    reason=(
                        f"Role rule: "
                        f"level={request.agent_level.value}"
                        f", model={model.model_id}"
                    ),
                    fallbacks_tried=tried,
                )
    logger.debug(
        ROUTING_NO_RULE_MATCHED,
        agent_level=request.agent_level.value,
        strategy=strategy_name,
        source="role_rules",
    )
    return None


def _try_seniority_default(
    request: RoutingRequest,
    resolver: ModelResolver,
    strategy_name: str,
) -> RoutingDecision | None:
    """Try seniority catalog tier; return decision or None."""
    if request.agent_level is None:
        return None
    try:
        tier = get_seniority_info(request.agent_level).typical_model_tier
    except LookupError:
        logger.warning(
            ROUTING_NO_RULE_MATCHED,
            level=request.agent_level.value,
            strategy=strategy_name,
            reason="seniority level not in catalog",
        )
        return None
    model = resolver.resolve_safe(tier)
    if model is not None:
        return RoutingDecision(
            resolved_model=model,
            strategy_used=strategy_name,
            reason=(
                f"Seniority default: level={request.agent_level.value}, tier={tier}"
            ),
        )
    logger.info(
        ROUTING_NO_RULE_MATCHED,
        level=request.agent_level.value,
        tier=tier,
        strategy=strategy_name,
        reason="seniority tier not registered",
    )
    return None
