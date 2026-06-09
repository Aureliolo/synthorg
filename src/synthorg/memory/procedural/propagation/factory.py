"""Factory for building propagation strategies from configuration."""

from synthorg.core.registry import StrategyRegistry
from synthorg.memory.procedural.propagation.config import PropagationConfig
from synthorg.memory.procedural.propagation.department_scoped import (
    DepartmentScopedPropagation,
)
from synthorg.memory.procedural.propagation.no_propagation import (
    NoPropagation,
)
from synthorg.memory.procedural.propagation.protocol import PropagationStrategy
from synthorg.memory.procedural.propagation.role_scoped import (
    RoleScopedPropagation,
)
from synthorg.observability import get_logger

logger = get_logger(__name__)


def _build_none(_config: PropagationConfig) -> PropagationStrategy:
    """Registry entry: build the no-op propagation strategy.

    Returns:
        A ``NoPropagation`` that never shares memories.
    """
    return NoPropagation()


def _build_role_scoped(config: PropagationConfig) -> PropagationStrategy:
    """Registry entry: build the role-scoped propagation strategy.

    Returns:
        A ``RoleScopedPropagation`` capped at ``max_propagation_targets``.
    """
    return RoleScopedPropagation(max_targets=config.max_propagation_targets)


def _build_department_scoped(config: PropagationConfig) -> PropagationStrategy:
    """Registry entry: build the department-scoped propagation strategy.

    Returns:
        A ``DepartmentScopedPropagation`` capped at ``max_propagation_targets``.
    """
    return DepartmentScopedPropagation(max_targets=config.max_propagation_targets)


_REGISTRY: StrategyRegistry[PropagationStrategy] = StrategyRegistry(
    {
        "none": _build_none,
        "role_scoped": _build_role_scoped,
        "department_scoped": _build_department_scoped,
    },
    kind="propagation",
)


def build_propagation_strategy(
    config: PropagationConfig,
) -> PropagationStrategy:
    """Build a propagation strategy from configuration.

    Args:
        config: Propagation strategy configuration.

    Returns:
        Configured propagation strategy instance.

    Raises:
        StrategyFactoryNotFoundError: If ``config.type`` is not registered.
    """
    return _REGISTRY.build(config.type, config)
