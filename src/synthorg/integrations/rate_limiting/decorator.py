"""Tool-side rate limiting decorator.

Applies per-connection rate limits to tool implementations using
the existing ``RateLimiter`` from the provider resilience layer.
"""

import functools
from collections.abc import Callable, Coroutine
from typing import ParamSpec, TypeVar

from synthorg.core.resilience_config import RateLimiterConfig
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    TOOL_RATE_LIMIT_ACQUIRED,
)
from synthorg.providers.resilience.rate_limiter import RateLimiter

logger = get_logger(__name__)

P = ParamSpec("P")
T = TypeVar("T")

_limiters: dict[tuple[str, str], RateLimiter] = {}


def _config_signature(config: RateLimiterConfig) -> str:
    """Stable hash of the rate-limiter config.

    Used as part of the limiter cache key so different
    ``RateLimiterConfig`` instances targeting the same connection
    get distinct limiters instead of silently reusing whichever
    config was cached first.

    Returns:
        A stable JSON string uniquely representing the config instance.
    """
    return config.model_dump_json()


def _get_or_create_limiter(
    connection_name: str,
    config: RateLimiterConfig,
) -> RateLimiter:
    """Get or create a rate limiter for a connection.

    Returns:
        The existing or newly-created ``RateLimiter`` for the
        ``(connection_name, config_signature)`` key.
    """
    key = (connection_name, _config_signature(config))
    if key not in _limiters:
        _limiters[key] = RateLimiter(
            config,
            provider_name=f"connection:{connection_name}",
        )
    return _limiters[key]


def with_connection_rate_limit(
    connection_name: str,
    *,
    config: RateLimiterConfig | None = None,
) -> Callable[
    [Callable[P, Coroutine[object, object, T]]],
    Callable[P, Coroutine[object, object, T]],
]:
    """Decorator that applies connection-level rate limiting to a tool.

    Wraps an async tool method with ``RateLimiter.acquire()`` /
    ``release()`` calls using the connection's configured rate limit.

    Args:
        connection_name: Connection name to rate-limit by.
        config: Rate limiter config override.  If ``None``, uses a
            default of 60 RPM / 0 concurrency.

    Returns:
        A decorator that wraps the async function.

    Example::

        @with_connection_rate_limit("github")
        async def fetch_github_pr(self, repo: str) -> str: ...
    """
    effective_config = config or RateLimiterConfig(
        max_requests_per_minute=60,
    )
    config_was_explicit = config is not None

    def decorator(
        fn: Callable[P, Coroutine[object, object, T]],
    ) -> Callable[P, Coroutine[object, object, T]]:

        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            """Acquire the connection's rate-limit slot, then run ``fn``.

            Returns:
                The wrapped coroutine's result, unchanged.
            """
            from synthorg.integrations.rate_limiting.shared_state import (  # noqa: PLC0415
                get_coordinator,
            )

            coordinator = get_coordinator(connection_name)
            if coordinator is not None:
                await coordinator.acquire()
                logger.debug(
                    TOOL_RATE_LIMIT_ACQUIRED,
                    connection_name=connection_name,
                )
                # When the caller passed an explicit ``config=``,
                # also honour it via the local token-bucket limiter
                # so workload-specific overrides are not silently
                # dropped just because a cross-worker coordinator
                # exists. The coordinator enforces the connection-
                # wide fair share; the local limiter then enforces
                # the caller's narrower per-tool cap on top.
                if config_was_explicit:
                    limiter = _get_or_create_limiter(
                        connection_name,
                        effective_config,
                    )
                    if limiter.is_enabled:
                        await limiter.acquire()
                        try:
                            return await fn(*args, **kwargs)
                        finally:
                            limiter.release()
                return await fn(*args, **kwargs)

            limiter = _get_or_create_limiter(
                connection_name,
                effective_config,
            )
            if not limiter.is_enabled:
                return await fn(*args, **kwargs)

            await limiter.acquire()
            logger.debug(
                TOOL_RATE_LIMIT_ACQUIRED,
                connection_name=connection_name,
            )
            try:
                return await fn(*args, **kwargs)
            finally:
                limiter.release()

        return wrapper

    return decorator
