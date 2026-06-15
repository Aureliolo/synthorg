# module-kind: code
"""Replan-hook factory: selects a ``CoordinationReplanHook`` by strategy.

The coordination middleware chain wires one replan hook at boot. The
``replan_strategy`` discriminator (``noop`` / ``magentic``) selects which
hook is built; ``noop`` is the safe default (never replans), so standing
the chain up changes no behaviour until an operator opts into
``magentic``. The magentic hook is parameterised by the resolved
``max_stall_count`` / ``max_reset_count`` caps and an optional
``BudgetEnforcer`` for replan-affordability checks.
"""

from synthorg.budget.affordability import BudgetAffordabilityChecker
from synthorg.core.registry.strategy import StrategyRegistry
from synthorg.engine.middleware.coordination_constraints import (
    CoordinationReplanHook,
    MagenticReplanHook,
    NoOpReplanHook,
)


def _build_noop(**_kwargs: object) -> CoordinationReplanHook:
    """Build the no-op replan hook (ignores caps / enforcer).

    Returns:
        A :class:`NoOpReplanHook` that never replans.
    """
    return NoOpReplanHook()


def _build_magentic(
    *,
    max_stall_count: int,
    max_reset_count: int,
    budget_enforcer: BudgetAffordabilityChecker | None = None,
    **_kwargs: object,
) -> CoordinationReplanHook:
    """Build the magentic stall-detecting replan hook from the caps.

    Returns:
        A :class:`MagenticReplanHook` parameterised by the caps and the
        optional affordability checker.
    """
    return MagenticReplanHook(
        max_stall_count=max_stall_count,
        max_reset_count=max_reset_count,
        budget_enforcer=budget_enforcer,
    )


_REPLAN_REGISTRY: StrategyRegistry[CoordinationReplanHook] = StrategyRegistry(
    {
        "noop": _build_noop,
        "magentic": _build_magentic,
    },
    kind="replan_strategy",
)


def create_replan_hook(
    strategy: str,
    *,
    max_stall_count: int,
    max_reset_count: int,
    budget_enforcer: BudgetAffordabilityChecker | None = None,
) -> CoordinationReplanHook:
    """Build the coordination replan hook for *strategy*.

    Args:
        strategy: The ``replan_strategy`` discriminator. ``noop`` is the
            safe default (never replans); ``magentic`` triggers stall-
            driven replans up to the caps.
        max_stall_count: Maximum consecutive stalls before escalation
            (consumed only by the magentic hook).
        max_reset_count: Maximum replan cycles before escalation
            (consumed only by the magentic hook).
        budget_enforcer: Optional enforcer for replan affordability
            checks; when absent the magentic hook skips the check.

    Returns:
        The selected :class:`CoordinationReplanHook`.

    Raises:
        StrategyFactoryNotFoundError: When *strategy* is not a known
            discriminator.
    """
    return _REPLAN_REGISTRY.build(
        strategy,
        max_stall_count=max_stall_count,
        max_reset_count=max_reset_count,
        budget_enforcer=budget_enforcer,
    )
