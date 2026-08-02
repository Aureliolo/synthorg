"""Shared auth-endpoint primitives: rate limiter, dummy hash, lockout helpers.

Pure helper module consumed by the auth sub-controllers. The single
``_AUTH_RATE_LIMIT`` instance is imported (not re-instantiated) by the
bootstrap, session, and credentials controllers so they share ONE
counter store; the login-attempt helpers + constant-time dummy hash are
used by the session controller's login flow.
"""

from typing import Final

from litestar import Request
from litestar.datastructures import State

import synthorg.settings.definitions.api  # noqa: F401 -- populate the API registry
from synthorg.api.api_core_state import ApiCoreStateSlice, lockout_store_of
from synthorg.api.rate_limits.live_global import (
    LiveRateLimitConfig,
    RateLimitTier,
)
from synthorg.api.state import AppState
from synthorg.config.rate_limits import AUTH_ENDPOINT_WINDOW
from synthorg.core.domain_errors import AccountLockedError
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_AUTH_ACCOUNT_LOCKED,
    SECURITY_AUTH_LOCKOUT_CLEARED,
)
from synthorg.observability.metrics_hub import (
    record_auth_failure,
    record_auth_lockout,
)
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import parse_int

logger = get_logger(__name__)

# Pre-computed Argon2id hash for constant-time rejection when the
# username doesn't exist -- prevents timing-based username enumeration.
# The actual password is irrelevant; only the verification time matters.
_DUMMY_ARGON2_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHRzYWx0$"
    "mB0bZKSNwOhSdxMQfsldT3qGmFyjVqbkntMkutMfdUs"
)


# Auth endpoints get a stricter throttle than the global limiter so a
# brute-force login / setup loop is bounded regardless of the global cap.
# This is the fallback the middleware enforces before the boot snapshot of
# the live caps lands; after that the cap is read per request.
AUTH_RATE_LIMIT_PER_MINUTE: Final[int] = int(
    resolve_init_value(
        SettingNamespace.API,
        "rate_limit_auth_endpoint_max_requests",
        parse=parse_int,
    ).value
)


_AUTH_RATE_LIMIT = LiveRateLimitConfig(
    rate_limit=(AUTH_ENDPOINT_WINDOW, AUTH_RATE_LIMIT_PER_MINUTE),
    tier=RateLimitTier.AUTH_ENDPOINT,
)
"""Stricter rate limiter for auth endpoints (10 req/min).

Applied as route-level middleware on ``/auth/login``,
``/auth/setup``, and ``/auth/change-password``.  Keyed by remote
IP (``request.client.host``).  Each ``RateLimitConfig`` instance
produces a middleware with an independent store, so counters do
not collide with the global rate limiter.

.. note::

   Behind a reverse proxy (e.g. nginx in Docker), Litestar's
   default ``get_remote_address`` reads ``request.client.host``
   which is the proxy IP, not the real client.  Enable Uvicorn's
   ``--proxy-headers`` and ``--forwarded-allow-ips`` to trust
   ``X-Forwarded-For`` from the proxy.
"""


async def _record_failed_login(
    app_state: AppState,
    username: str,
    request: Request[object, object, State],
) -> None:
    """Record a failed login attempt and raise on lockout.

    Per the persistence-boundary rule the repo does not log
    ``SECURITY_AUTH_ACCOUNT_LOCKED``; this helper emits the signed audit
    event with the controller-side context (threshold + duration) and
    raises ``AccountLockedError`` so the caller's flow short-circuits
    before the generic ``invalid_credentials`` log line.

    Raises:
        AccountLockedError: Raised on the corresponding failure path.
    """
    # Count every failed credential check, independent of whether a lockout
    # store is wired, so the auth-failure counter reflects real attempts.
    record_auth_failure(reason="invalid_password")
    if app_state.slice(ApiCoreStateSlice).lockout_store is None:
        return
    client = request.client
    ip = client.host if client else ""
    locked = await lockout_store_of(app_state).record_failure(
        username,
        ip_address=ip,
    )
    if not locked:
        return
    logger.warning(
        SECURITY_AUTH_ACCOUNT_LOCKED,
        username=username,
        threshold=lockout_store_of(app_state).threshold,
        duration_minutes=(lockout_store_of(app_state).lockout_duration_seconds // 60),
    )
    record_auth_lockout()
    raise AccountLockedError(
        retry_after=lockout_store_of(app_state).lockout_duration_seconds,
    )


async def _record_successful_login(app_state: AppState, username: str) -> None:
    """Clear the lockout state and emit the audit event when warranted.

    Repo returns whether a lock was actually cleared; we only emit
    ``SECURITY_AUTH_LOCKOUT_CLEARED`` when there was something to
    clear so a routine successful login does not chain a no-op
    decision into the audit log.
    """
    if app_state.slice(ApiCoreStateSlice).lockout_store is None:
        return
    if await lockout_store_of(app_state).record_success(username):
        logger.info(
            SECURITY_AUTH_LOCKOUT_CLEARED,
            username=username,
        )
