"""Which rate-limit tier a request belongs to, and what keys it.

Two tiers share every request between them: anonymous, keyed by client IP and
sized for a stranger, and authenticated, keyed by user and sized for someone
doing work. Exactly one must claim each request -- neither leaves an endpoint
unlimited, both halve its real budget -- so the two gates are written as
complements and live together rather than beside the middleware assembly that
consumes them.
"""

import re
from typing import Final

from litestar import Request
from litestar.datastructures import State
from litestar.middleware.rate_limit import get_remote_address

# Matched against the request path rather than derived from the configured API
# prefix: the gates are plain predicates the rate-limit middleware calls, with
# no access to the config that built them. Both routes are anchored the same
# way their auth exclusions are, so no sibling route inherits the tier.
_SELF_AUTHENTICATED_PATHS: Final[re.Pattern[str]] = re.compile(
    r"/(gateway|mcp-gateway)(/|$)"
)


def auth_identifier_for_request(
    request: Request[object, object, State],
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


def bears_own_credential(request: Request[object, object, State]) -> bool:
    """Report whether the path authenticates with a per-run signed bearer.

    The LLM gateway and the credentialed-tool MCP server verify their own
    bearer inside the handler, which is why both are auth-excluded, so
    ``scope["user"]`` is never populated for them. They are not anonymous
    traffic though, and the anonymous tier's cap is sized for a stranger with
    an IP: an agent doing ordinary work spends it in seconds and the run dies
    on a 429 from its own control plane.

    Args:
        request: The incoming request.

    Returns:
        ``True`` when the request carries its own verified-in-handler bearer.
    """
    return _SELF_AUTHENTICATED_PATHS.search(request.scope["path"]) is not None


def throttle_when_anonymous(
    request: Request[object, object, State],
) -> bool:
    """Throttle-gate for the anonymous tier.

    The auth middleware runs before the rate-limit middleware (see middleware
    order at the bottom of ``build_middleware``), so ``scope["user"]`` is
    authoritatively populated -- either the real ``AuthenticatedUser`` after
    JWT/API-key verification, or ``None`` for auth-excluded paths
    (``/auth/login``, ``/auth/setup`` etc.) which the auth middleware skips. A
    forged session cookie cannot bypass this check: if the JWT didn't verify,
    auth either raised 401 before we got here or left ``user`` unset.

    Args:
        request: The incoming request.

    Returns:
        ``True`` when the request counts against the anonymous bucket,
        ``False`` when the per-user tier should handle it instead.
    """
    if bears_own_credential(request):
        return False
    return request.scope.get("user") is None


def throttle_when_authenticated(
    request: Request[object, object, State],
) -> bool:
    """Throttle-gate for the authenticated tier (per user).

    Mirror of :func:`throttle_when_anonymous`. Ensures anonymous traffic on
    auth-excluded paths is counted by the anonymous tier only, not
    double-counted under its fallback IP identifier.

    Args:
        request: The incoming request.

    Returns:
        ``True`` when the request counts against the per-user bucket.
    """
    if bears_own_credential(request):
        return True
    return request.scope.get("user") is not None
