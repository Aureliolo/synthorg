"""Middleware chain factories.

Build agent and coordination middleware chains from configuration.
Resolves each middleware name via the registry, instantiates with
available dependencies, and composes into chains.

Middleware whose required dependencies are unavailable is skipped
with a DEBUG log (safe default = opt-out).
"""

from typing import TYPE_CHECKING, Any

from synthorg.engine.middleware.coordination_protocol import (
    CoordinationMiddleware,
    CoordinationMiddlewareChain,
)
from synthorg.engine.middleware.errors import MiddlewareRegistryError
from synthorg.engine.middleware.protocol import (
    AgentMiddleware,
    AgentMiddlewareChain,
)
from synthorg.engine.middleware.registry import (
    get_agent_middleware_factory,
    get_coordination_middleware_factory,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.middleware import (
    MIDDLEWARE_CHAIN_BUILT,
    MIDDLEWARE_COORDINATION_CHAIN_BUILT,
    MIDDLEWARE_COORDINATION_SKIPPED,
    MIDDLEWARE_SKIPPED,
)

if TYPE_CHECKING:
    from synthorg.engine.middleware.config import (
        AgentMiddlewareConfig,
        CoordinationMiddlewareConfig,
    )

logger = get_logger(__name__)


def build_agent_middleware_chain(
    config: AgentMiddlewareConfig,
    *,
    deps: dict[str, Any] | None = None,
) -> AgentMiddlewareChain:
    """Build an agent middleware chain from configuration.

    Resolves each name in ``config.chain`` via the agent registry,
    instantiates with ``deps``, and composes into a chain.

    Middleware whose factory raises ``TypeError`` (missing required
    dependency) is skipped with a DEBUG log.  Middleware whose name
    is not registered is also skipped (not an error -- allows
    incremental registration).

    Args:
        config: Agent middleware configuration.
        deps: Keyword arguments forwarded to each factory callable.

    Returns:
        Composed ``AgentMiddlewareChain``.
    """
    middleware: list[AgentMiddleware] = []
    effective_deps = deps or {}

    for name in config.chain:
        try:
            factory = get_agent_middleware_factory(name)
        except MiddlewareRegistryError:
            logger.debug(
                MIDDLEWARE_SKIPPED,
                middleware=name,
                reason="not_registered",
            )
            continue

        try:
            mw = factory(**effective_deps)
        except TypeError as exc:
            # Only skip if the TypeError is from missing factory args,
            # not from internal bugs in the factory
            if "argument" not in str(exc) and "parameter" not in str(exc):
                logger.exception(
                    MIDDLEWARE_SKIPPED,
                    middleware=name,
                    reason=f"factory_error: {safe_error_description(exc)}",
                )
                raise
            logger.debug(
                MIDDLEWARE_SKIPPED,
                middleware=name,
                reason=f"missing_dependency: {safe_error_description(exc)}",
            )
            continue

        middleware.append(mw)

    chain = AgentMiddlewareChain(tuple(middleware))
    built_names = list(chain.names)
    skipped_names: list[str] = []
    for requested_name in config.chain:
        if requested_name in built_names:
            built_names.remove(requested_name)
        else:
            skipped_names.append(requested_name)
    logger.info(
        MIDDLEWARE_CHAIN_BUILT,
        chain_names=chain.names,
        chain_length=len(chain),
        requested=config.chain,
        skipped=tuple(skipped_names),
    )
    return chain


def build_coordination_middleware_chain(
    config: CoordinationMiddlewareConfig,
    *,
    deps: dict[str, Any] | None = None,
) -> CoordinationMiddlewareChain:
    """Build a coordination middleware chain from configuration.

    Same resolution and skip semantics as
    ``build_agent_middleware_chain``.

    Args:
        config: Coordination middleware configuration.
        deps: Keyword arguments forwarded to each factory callable.

    Returns:
        Composed ``CoordinationMiddlewareChain``.
    """
    middleware: list[CoordinationMiddleware] = []
    effective_deps = deps or {}

    for name in config.chain:
        try:
            factory = get_coordination_middleware_factory(name)
        except MiddlewareRegistryError:
            logger.debug(
                MIDDLEWARE_COORDINATION_SKIPPED,
                middleware=name,
                reason="not_registered",
            )
            continue

        try:
            mw = factory(**effective_deps)
        except TypeError as exc:
            if "argument" not in str(exc) and "parameter" not in str(exc):
                logger.exception(
                    MIDDLEWARE_COORDINATION_SKIPPED,
                    middleware=name,
                    reason=f"factory_error: {safe_error_description(exc)}",
                )
                raise
            logger.debug(
                MIDDLEWARE_COORDINATION_SKIPPED,
                middleware=name,
                reason=f"missing_dependency: {safe_error_description(exc)}",
            )
            continue

        middleware.append(mw)

    chain = CoordinationMiddlewareChain(tuple(middleware))
    built_names = list(chain.names)
    skipped_names: list[str] = []
    for requested_name in config.chain:
        if requested_name in built_names:
            built_names.remove(requested_name)
        else:
            skipped_names.append(requested_name)
    logger.info(
        MIDDLEWARE_COORDINATION_CHAIN_BUILT,
        chain_names=chain.names,
        chain_length=len(chain),
        requested=config.chain,
        skipped=tuple(skipped_names),
    )
    return chain
