"""Middleware factory helpers for the Litestar application.

Builds the rate-limit + auth + CSRF middleware stack and the client
identifier extractors the rate-limit tiers use.
"""

import ipaddress
from collections.abc import Callable  # noqa: TC003
from typing import TYPE_CHECKING, Any

from litestar import Request  # noqa: TC002
from litestar.middleware.rate_limit import (
    RateLimitConfig as LitestarRateLimitConfig,
)
from litestar.middleware.rate_limit import (
    get_remote_address,
)

from synthorg.api.auth.csrf import create_csrf_middleware_class
from synthorg.api.auth.middleware import create_auth_middleware_class
from synthorg.api.etag import ETagMiddleware
from synthorg.api.middleware import RequestLoggingMiddleware
from synthorg.api.rate_limits import PerOpConcurrencyMiddleware
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_NETWORK_EXPOSURE_WARNING

if TYPE_CHECKING:
    from litestar.types import Middleware

    from synthorg.api.config import ApiConfig
    from synthorg.core.auth.config import AuthConfig

logger = get_logger(__name__)


def _parse_trusted_networks(
    trusted: frozenset[str],
) -> tuple[
    tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
    tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
]:
    """Parse trusted proxy entries into network + address tuples.

    Accepts both single IPs (``10.0.0.5``) and CIDR blocks
    (``10.0.0.0/8``, ``::1/128``). Invalid entries are dropped with a
    warning so a typo cannot silently permit every proxy.
    """
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for entry in trusted:
        if "/" in entry:
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                logger.warning(
                    API_NETWORK_EXPOSURE_WARNING,
                    note=f"Dropping invalid CIDR in trusted_proxies: {entry!r}",
                )
            continue
        try:
            addresses.append(ipaddress.ip_address(entry))
        except ValueError:
            logger.warning(
                API_NETWORK_EXPOSURE_WARNING,
                note=f"Dropping invalid IP in trusted_proxies: {entry!r}",
            )
    return tuple(networks), tuple(addresses)


def _ip_is_trusted(
    ip_str: str,
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
    addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
) -> bool:
    """Return True when ``ip_str`` matches a trusted IP or CIDR."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if ip in addresses:
        return True
    return any(ip in net for net in networks)


def _build_unauth_identifier(
    trusted: frozenset[str],
) -> Callable[[Request[Any, Any, Any]], str]:
    """Build a proxy-aware client IP extractor for the unauth tier.

    When ``trusted_proxies`` is configured, extracts the real client
    IP from the ``X-Forwarded-For`` header (rightmost untrusted hop).
    Without trusted proxies, falls back to ``request.client.host``.

    Trusted entries may be individual IPs or CIDR blocks
    (``10.0.0.0/8``); CIDR matching uses :mod:`ipaddress` membership.

    Args:
        trusted: Frozen set of trusted proxy IPs/CIDRs.

    Returns:
        Callable that extracts a rate-limit key from a request.
    """
    if not trusted:
        return get_remote_address

    networks, addresses = _parse_trusted_networks(trusted)
    if not networks and not addresses:
        return get_remote_address

    def _extract_forwarded_ip(
        request: Request[Any, Any, Any],
    ) -> str:
        # Only trust X-Forwarded-For when the immediate peer is a
        # known proxy. Otherwise any client can spoof the header.
        peer_ip = get_remote_address(request)
        if not _ip_is_trusted(peer_ip, networks, addresses):
            return peer_ip
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            # X-Forwarded-For: client, proxy1, proxy2
            # Walk from the right, skip trusted proxies.
            hops = [h.strip() for h in forwarded.split(",")]
            for hop in reversed(hops):
                if not _ip_is_trusted(hop, networks, addresses):
                    return hop
        return peer_ip

    return _extract_forwarded_ip


def _auth_identifier_for_request(
    request: Request[Any, Any, Any],
) -> str:
    """Return the authenticated user's ID as the rate limit key.

    Falls back to client IP when the user is not set in scope
    (e.g. auth-excluded paths that are not excluded from the
    auth rate limiter).

    Args:
        request: The incoming request.

    Returns:
        User ID string or client IP as fallback.
    """
    user = request.scope.get("user")
    if user is not None and hasattr(user, "user_id"):
        return str(user.user_id)
    return get_remote_address(request)


def _throttle_when_anonymous(
    request: Request[Any, Any, Any],
) -> bool:
    """Throttle-gate for the anonymous tier.

    The auth middleware runs before the rate-limit middleware (see
    middleware order at the bottom of :func:`build_middleware`), so
    ``scope["user"]`` is authoritatively populated -- either the real
    ``AuthenticatedUser`` after JWT/API-key verification, or ``None``
    for auth-excluded paths (``/auth/login``, ``/auth/setup`` etc.)
    which the auth middleware skips.  A forged session cookie cannot
    bypass this check: if the JWT didn't verify, auth either raised
    401 before we got here or left ``user`` unset.

    Returns ``True`` when the request should count against the
    anonymous bucket, ``False`` when the per-user (auth) tier should
    handle it instead.
    """
    return request.scope.get("user") is None


def _throttle_when_authenticated(
    request: Request[Any, Any, Any],
) -> bool:
    """Throttle-gate for the authenticated tier (per user).

    Mirror of :func:`_throttle_when_anonymous`.  Ensures anonymous
    traffic on auth-excluded paths is counted by the anonymous tier
    only, not double-counted under its fallback IP identifier.
    """
    return request.scope.get("user") is not None


def _build_auth_exclude_paths(
    auth: AuthConfig,
    prefix: str,
    ws_path: str,
    *,
    a2a_enabled: bool = False,
) -> tuple[str, ...]:
    """Compute auth middleware exclude paths with fail-safe defaults."""
    setup_status_path = f"^{prefix}/setup/status$"
    metrics_path = f"^{prefix}/metrics$"
    # Logout must always bypass auth so clients can recover from
    # stale cookie state (an app-version upgrade invalidates the
    # session without giving the client a way to clear it).  Kept
    # as a fail-safe even when operators override
    # ``auth.exclude_paths`` with a custom list.
    logout_path = f"^{prefix}/auth/logout$"
    # The OAuth provider redirects the user's browser here without a
    # session cookie, so the global auth middleware has to let it
    # through. CSRF protection is handled by the state token the
    # callback validates against the oauth_states repo.
    oauth_callback_path = f"^{prefix}/oauth/callback$"
    # Liveness / readiness probes are always excluded from auth so
    # supervisors (k8s, docker-healthcheck, CLI status polling) can
    # reach them without credentials.  A custom ``auth.exclude_paths``
    # could otherwise silently start returning 401 on probes and break
    # restart loops -- treat these as fail-safe additions alongside
    # metrics / setup-status / logout.
    healthz_path = f"^{prefix}/healthz$"
    readyz_path = f"^{prefix}/readyz$"
    exclude_paths = (
        auth.exclude_paths
        if auth.exclude_paths is not None
        else (
            healthz_path,
            readyz_path,
            metrics_path,
            "^/docs",
            "^/api$",
            f"^{prefix}/auth/setup$",
            f"^{prefix}/auth/login$",
            logout_path,
            setup_status_path,
            oauth_callback_path,
        )
    )
    # Fold all mandatory fail-safe paths through a single helper so
    # each addition stays O(1) and the function body keeps below
    # ruff's complexity ceiling.  ``/auth/setup`` + ``/auth/login``
    # stay in the list so a custom ``auth.exclude_paths`` never
    # locks operators out of the initial setup / recovery flows:
    # the auth middleware cannot authenticate a user without a way
    # to set or recover credentials first.
    auth_setup_path = f"^{prefix}/auth/setup$"
    auth_login_path = f"^{prefix}/auth/login$"
    mandatory_paths: list[str] = [
        healthz_path,
        readyz_path,
        metrics_path,
        setup_status_path,
        logout_path,
        auth_setup_path,
        auth_login_path,
        ws_path,
        oauth_callback_path,
    ]
    if a2a_enabled:
        mandatory_paths.extend((f"^{prefix}/a2a", r"^/\.well-known"))
    for path in mandatory_paths:
        if path not in exclude_paths:
            exclude_paths = (*exclude_paths, path)
    return exclude_paths


def _warn_if_untrusted_proxies(api_config: ApiConfig) -> None:
    """Warn when no trusted proxies are configured for a non-local host."""
    trusted = frozenset(api_config.server.trusted_proxies)
    if not trusted and api_config.server.host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            API_NETWORK_EXPOSURE_WARNING,
            note=(
                "No trusted_proxies configured. If this server is behind "
                "a reverse proxy or load balancer, all proxied clients "
                "will share a single unauth rate-limit bucket. Set "
                "api.server.trusted_proxies to the proxy IPs."
            ),
        )


def _build_rate_limits(
    api_config: ApiConfig,
    *,
    ws_path: str,
    unauth_identifier: Callable[[Request[Any, Any, Any]], str],
) -> tuple[LitestarRateLimitConfig, LitestarRateLimitConfig, LitestarRateLimitConfig]:
    """Build the three rate-limit tiers (IP floor, unauth, auth)."""
    rl = api_config.rate_limit
    rl_exclude = list(rl.exclude_paths)
    if ws_path not in rl_exclude:
        rl_exclude.append(ws_path)

    ip_floor = LitestarRateLimitConfig(
        rate_limit=(rl.time_unit, rl.floor_max_requests),  # type: ignore[arg-type]
        exclude=rl_exclude,
        identifier_for_request=unauth_identifier,
        store="rate_limit_floor",
    )
    unauth = LitestarRateLimitConfig(
        rate_limit=(rl.time_unit, rl.unauth_max_requests),  # type: ignore[arg-type]
        exclude=rl_exclude,
        identifier_for_request=unauth_identifier,
        check_throttle_handler=_throttle_when_anonymous,
        store="rate_limit_unauth",
    )
    auth = LitestarRateLimitConfig(
        rate_limit=(rl.time_unit, rl.auth_max_requests),  # type: ignore[arg-type]
        exclude=rl_exclude,
        identifier_for_request=_auth_identifier_for_request,
        check_throttle_handler=_throttle_when_authenticated,
        store="rate_limit_auth",
    )
    return ip_floor, unauth, auth


def _build_auth_and_csrf(
    api_config: ApiConfig,
    *,
    prefix: str,
    ws_path: str,
    a2a_enabled: bool,
) -> tuple[Middleware, Middleware]:
    """Build the auth and CSRF middleware classes."""
    exclude_paths = _build_auth_exclude_paths(
        api_config.auth,
        prefix,
        ws_path,
        a2a_enabled=a2a_enabled,
    )
    auth = api_config.auth.model_copy(
        update={"exclude_paths": exclude_paths},
    )
    auth_middleware = create_auth_middleware_class(auth)

    # CSRF middleware: exempt login/setup (they set the cookie, client
    # cannot carry a CSRF token on the first request), logout (clients
    # may need to clear a stale session whose CSRF cookie was lost;
    # e.g. on app version upgrade; CSRF-protecting logout is low value
    # since forcing a logout is a nuisance, not a compromise), and
    # health.
    csrf_exempt = frozenset(
        {
            f"{prefix}/auth/login",
            f"{prefix}/auth/setup",
            f"{prefix}/auth/logout",
            f"{prefix}/healthz",
            f"{prefix}/readyz",
        }
    )
    csrf_middleware = create_csrf_middleware_class(auth, exempt_paths=csrf_exempt)
    return auth_middleware, csrf_middleware


def _build_middleware(
    api_config: ApiConfig,
    *,
    a2a_enabled: bool = False,
    rate_limiter_enabled: bool = True,
) -> list[Middleware]:
    """Build the middleware stack from configuration.

    Three rate-limit tiers surround the auth middleware:

    1. **IP floor** (outermost) -- keyed by client IP, high budget,
       un-gated.  Counts every request that reaches the app, including
       ones the auth middleware will reject with 401.  Protects
       against floods of forged-token traffic on protected endpoints.
    2. Auth middleware -- populates ``scope["user"]``.
    3. CSRF middleware -- double-submit validation for cookie
       sessions (exempt login/setup/logout/health).
    4. **Unauth tier** -- keyed by client IP, low budget, fires only
       when ``scope["user"]`` is ``None`` (aggressive cap on
       brute-force against login/setup).
    5. Request logging.
    6. **Auth tier** (innermost) -- keyed by user ID, high budget,
       fires only when ``scope["user"]`` is set.  Prevents a single
       authenticated user from abusing the API.

    When ``trusted_proxies`` is configured, IP-based tiers read
    ``X-Forwarded-For`` to extract the real client IP. Without it,
    all clients behind a proxy share one IP-based rate limit bucket.
    """
    prefix = api_config.api_prefix
    ws_path = f"^{prefix}/ws$"
    auth_middleware, csrf_middleware = _build_auth_and_csrf(
        api_config,
        prefix=prefix,
        ws_path=ws_path,
        a2a_enabled=a2a_enabled,
    )

    # Middleware order (outside-in, i.e. request flow):
    #   1. ip_floor -- un-gated IP cap; counts every request
    #   2. ETagMiddleware -- conditional GET / 304 short-circuit on
    #      allowlisted read-only endpoints. Today this is a
    #      *bandwidth* optimisation only: the middleware computes the
    #      ETag from the rendered response body, so the inner stack
    #      (auth, handler, serialisation) still runs on every
    #      request. The 304 short-circuit only avoids the response
    #      body on the wire. Reaching the "cached read costs zero
    #      auth cycles" goal needs a stable validator (e.g. a
    #      per-resource version stamp) plumbed in front of the inner
    #      stack -- not scoped here.
    #   3. auth_middleware -- resolves identity, populates scope["user"]
    #   4. csrf_middleware -- validates double-submit for cookie sessions
    #   5. unauth_rl -- 20/min/IP for requests where user is None
    #   6. RequestLoggingMiddleware
    #   7. auth_rl -- per-user cap for authenticated requests
    #   8. PerOpConcurrencyMiddleware -- per-op inflight cap;
    #      innermost so ``scope["user"]`` is already populated and the
    #      permit is held only during actual handler execution.
    #
    # When ``api.rate_limiter_enabled`` is False (dev only, opt-in
    # via the registry), the three global rate-limit tiers are
    # omitted from the chain AND the rate-limit helpers
    # (`_build_unauth_identifier`, `_build_rate_limits`,
    # `_warn_if_untrusted_proxies`) are short-circuited so a broken
    # rate-limit config in dev cannot fail app construction.
    # PerOpConcurrencyMiddleware and the per-op guard surface still
    # apply since they have their own master switches.
    if rate_limiter_enabled:
        trusted = frozenset(api_config.server.trusted_proxies)
        _warn_if_untrusted_proxies(api_config)
        unauth_identifier = _build_unauth_identifier(trusted)
        ip_floor, unauth_rl, auth_rl = _build_rate_limits(
            api_config,
            ws_path=ws_path,
            unauth_identifier=unauth_identifier,
        )
        return [
            ip_floor.middleware,
            ETagMiddleware,
            auth_middleware,
            csrf_middleware,
            unauth_rl.middleware,
            RequestLoggingMiddleware,
            auth_rl.middleware,
            # Pass an instance: ``ASGIMiddleware`` (litestar 2.15+) is
            # instance-based, not class-based.  Stateless -- no per-request
            # state on the middleware itself (see its docstring).
            PerOpConcurrencyMiddleware(),
        ]
    # ETagMiddleware is not a rate-limit tier; keep it in both branches.
    # The three global rate-limit tiers (ip_floor / unauth_rl / auth_rl)
    # are omitted when the gate is False; ETag bandwidth optimisation
    # remains active.
    return [
        ETagMiddleware,
        auth_middleware,
        csrf_middleware,
        RequestLoggingMiddleware,
        PerOpConcurrencyMiddleware(),
    ]
