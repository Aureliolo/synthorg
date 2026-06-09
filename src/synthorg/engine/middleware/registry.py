"""Middleware registries for agent and coordination middleware.

Each registry maps a middleware name to a factory callable that
creates an instance.  Registration is idempotent (same name + same
factory = no-op); re-registering a name with a different factory
raises ``MiddlewareRegistryError``.
"""

from collections.abc import Callable

from synthorg.engine.middleware.coordination_protocol import (
    CoordinationMiddleware,
)
from synthorg.engine.middleware.errors import MiddlewareRegistryError
from synthorg.engine.middleware.protocol import AgentMiddleware
from synthorg.observability import get_logger
from synthorg.observability.events.middleware import (
    MIDDLEWARE_REGISTRATION_CONFLICT,
    MIDDLEWARE_UNKNOWN,
)

logger = get_logger(__name__)

# ── Agent middleware registry ─────────────────────────────────────

# Factory signature: (**deps) -> AgentMiddleware. Each registered factory
# is a distinct middleware class with its own ``__init__`` deps, dispatched
# via ``inspect.signature(factory).bind(**deps)``; ``Callable[..., X]`` is
# the correct type because no Protocol can express "any factory unified
# only by its return type" (a ``**deps: object`` Protocol fails
# contravariance against the concrete classes' specific keyword params).
type AgentMiddlewareFactory = Callable[..., AgentMiddleware]  # type: ignore[explicit-any]

_AGENT_REGISTRY: dict[str, AgentMiddlewareFactory] = {}


def register_agent_middleware(
    name: str,
    factory: AgentMiddlewareFactory,
) -> None:
    """Register an agent middleware factory by name.

    Idempotent: re-registering the same name with the same factory
    is a no-op.  Re-registering with a different factory raises
    ``MiddlewareRegistryError``.

    Args:
        name: Unique middleware name.
        factory: Callable that creates an ``AgentMiddleware`` instance.

    Raises:
        MiddlewareRegistryError: If ``name`` is already registered
            with a different factory.
    """
    existing = _AGENT_REGISTRY.get(name)
    if existing is not None:
        if existing is factory:
            return  # idempotent
        logger.warning(
            MIDDLEWARE_REGISTRATION_CONFLICT,
            name=name,
            registry_type="agent",
        )
        raise MiddlewareRegistryError(
            name,
            registry_type="agent",
        )
    _AGENT_REGISTRY[name] = factory


def get_agent_middleware_factory(
    name: str,
) -> AgentMiddlewareFactory:
    """Look up an agent middleware factory by name.

    Args:
        name: Middleware name.

    Returns:
        The registered factory callable.

    Raises:
        MiddlewareRegistryError: If ``name`` is not registered.
    """
    factory = _AGENT_REGISTRY.get(name)
    if factory is None:
        logger.warning(
            MIDDLEWARE_UNKNOWN,
            name=name,
            registry_type="agent",
        )
        raise MiddlewareRegistryError(
            name,
            registry_type="agent",
        )
    return factory


def registered_agent_middleware_names() -> tuple[str, ...]:
    """Return all registered agent middleware names."""
    return tuple(_AGENT_REGISTRY)


def clear_agent_registry() -> None:
    """Remove all agent middleware registrations (testing only)."""
    _AGENT_REGISTRY.clear()


# ── Coordination middleware registry ──────────────────────────────

# Same heterogeneous-factory shape as ``AgentMiddlewareFactory`` above.
type CoordinationMiddlewareFactory = Callable[  # type: ignore[explicit-any]
    ...,
    CoordinationMiddleware,
]

_COORDINATION_REGISTRY: dict[
    str,
    CoordinationMiddlewareFactory,
] = {}


def register_coordination_middleware(
    name: str,
    factory: CoordinationMiddlewareFactory,
) -> None:
    """Register a coordination middleware factory by name.

    Same idempotency semantics as ``register_agent_middleware``.

    Args:
        name: Unique middleware name.
        factory: Callable that creates a ``CoordinationMiddleware``.

    Raises:
        MiddlewareRegistryError: If ``name`` is already registered
            with a different factory.
    """
    existing = _COORDINATION_REGISTRY.get(name)
    if existing is not None:
        if existing is factory:
            return
        logger.warning(
            MIDDLEWARE_REGISTRATION_CONFLICT,
            name=name,
            registry_type="coordination",
        )
        raise MiddlewareRegistryError(
            name,
            registry_type="coordination",
        )
    _COORDINATION_REGISTRY[name] = factory


def get_coordination_middleware_factory(
    name: str,
) -> CoordinationMiddlewareFactory:
    """Look up a coordination middleware factory by name.

    Args:
        name: Middleware name.

    Returns:
        The registered factory callable.

    Raises:
        MiddlewareRegistryError: If ``name`` is not registered.
    """
    factory = _COORDINATION_REGISTRY.get(name)
    if factory is None:
        logger.warning(
            MIDDLEWARE_UNKNOWN,
            name=name,
            registry_type="coordination",
        )
        raise MiddlewareRegistryError(
            name,
            registry_type="coordination",
        )
    return factory


def registered_coordination_middleware_names() -> tuple[str, ...]:
    """Return all registered coordination middleware names."""
    return tuple(_COORDINATION_REGISTRY)


def clear_coordination_registry() -> None:
    """Remove all coordination middleware registrations (testing only)."""
    _COORDINATION_REGISTRY.clear()
