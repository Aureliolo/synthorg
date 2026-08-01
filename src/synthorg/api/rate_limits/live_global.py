# module-kind: adapter
"""Global rate-limit tiers that an operator can retune without a restart.

Litestar bakes ``max_requests`` into the middleware instance at
construction, so the caps an operator most wants to move (under exactly
the load that made them want to) were fixed for the life of the process.

This reads the cap per request from ``AppState`` instead. Everything else
-- the store, the sliding window, the response headers, the identifier and
throttle predicates -- stays Litestar's; only where the number comes from
changes.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import override

from litestar.middleware.rate_limit import (
    RateLimitConfig as LitestarRateLimitConfig,
)
from litestar.middleware.rate_limit import (
    RateLimitMiddleware,
)
from litestar.types import Receive, Scope, Send

from synthorg.api.state_per_op_limits import PerOpLimitsState
from synthorg.config.rate_limits import LiveRateLimits


class RateLimitTier(StrEnum):
    """Which cap a middleware instance enforces."""

    FLOOR = "floor"
    UNAUTH = "unauth"
    AUTH = "auth"
    AUTH_ENDPOINT = "auth_endpoint"


def _cap_for(config: LiveRateLimits, tier: RateLimitTier) -> int:
    """Return the configured cap for one tier.

    Args:
        config: The live tier config.
        tier: Which tier this middleware enforces.

    Returns:
        The maximum requests per window for that tier.
    """
    if tier is RateLimitTier.FLOOR:
        return config.floor_max_requests
    if tier is RateLimitTier.UNAUTH:
        return config.unauth_max_requests
    if tier is RateLimitTier.AUTH_ENDPOINT:
        return config.auth_endpoint_max_requests
    return config.auth_max_requests


class LiveRateLimitMiddleware(RateLimitMiddleware):
    """Rate-limit middleware whose cap is resolved per request.

    Mirrors Litestar's dispatch rather than extending it, because the cap
    is read inline there. Every helper it calls is the inherited one, so
    the window, the store and the headers behave identically.
    """

    @override
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Enforce the live cap for this tier, then hand off.

        Raises:
            TooManyRequestsException: When the caller is over its cap.
        """
        from litestar.exceptions import (  # noqa: PLC0415
            TooManyRequestsException,
        )

        app = scope["litestar_app"]
        request = app.request_class(scope)
        tier = self._tier()
        live = self._live_limits(scope)
        limit = self.max_requests if live is None else _cap_for(live, tier)
        # The credential-endpoint throttle is a brute-force bound, so the
        # general limiter's master switch and window do not reach it.
        live_window = live is not None and tier is not RateLimitTier.AUTH_ENDPOINT
        if live_window and live is not None and not live.enabled:
            # Disabled live. The window keeps running underneath, so
            # re-enabling does not hand everyone a fresh budget.
            await self.app(scope, receive, send)
            return
        store = self.config.get_store_from_app(app)
        if await self.should_check_request(request=request):
            key = self.cache_key_from_request(request)
            async with self._lock:
                # Assigned under the lock, not before it. This instance is
                # shared by every request on its tier, and the inherited
                # store helpers read ``self.unit`` when they run rather than
                # when it was set: assigning outside the lock lets a
                # concurrent request write a window against another
                # request's unit, expiring it early (a fresh budget
                # mid-window) or extending it.
                if live_window and live is not None:
                    self.unit = live.time_unit
                cache_object = await self.retrieve_cached_history(key, store)
                if len(cache_object.history) >= limit:
                    raise TooManyRequestsException(
                        headers=self.create_response_headers(cache_object=cache_object)
                        if self.config.set_rate_limit_headers
                        else None,
                    )
                await self.set_cached_history(
                    key=key, cache_object=cache_object, store=store
                )
            if self.config.set_rate_limit_headers:
                send = self.create_send_wrapper(send=send, cache_object=cache_object)
        await self.app(scope, receive, send)

    def _tier(self) -> RateLimitTier:
        """Return which cap this instance enforces.

        Returns:
            The bound tier, defaulting to the floor for a plain Litestar
            config that reached this class by some other route.
        """
        return getattr(self.config, "tier", RateLimitTier.FLOOR)

    def _live_limits(self, scope: Scope) -> LiveRateLimits | None:
        """Read the live tier config out of application state.

        Args:
            scope: The ASGI scope, carrying the Litestar app.

        Returns:
            The live config, or ``None`` before the boot snapshot lands.
            A caller arriving in that window is limited by the values
            Litestar was built with rather than by nothing at all.
        """
        app = scope.get("litestar_app")
        app_state = getattr(getattr(app, "state", None), "app_state", None)
        limits: PerOpLimitsState | None = getattr(app_state, "per_op_limits", None)
        if limits is None:
            return None
        return limits.global_config


@dataclass
class LiveRateLimitConfig(LitestarRateLimitConfig):  # type: ignore[explicit-any]
    """Litestar rate-limit config bound to one live tier.

    The ignore covers an inherited field: Litestar types
    ``check_throttle_handler`` as taking ``Request[Any, Any, Any]``, and the
    synthesised subclass carries that annotation into a tree that forbids an
    explicit ``Any``. Nothing here introduces one.

    Attributes:
        tier: Which cap :class:`LiveRateLimitMiddleware` reads per request.
    """

    tier: RateLimitTier = field(default=RateLimitTier.FLOOR)
    middleware_class: type[RateLimitMiddleware] = field(default=LiveRateLimitMiddleware)
