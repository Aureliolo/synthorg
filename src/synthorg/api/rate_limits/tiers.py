"""Which rate-limit tier a request belongs to, and what keys it.

Two tiers share every request between them: anonymous, keyed by client IP and
sized for a stranger, and authenticated, keyed by user and sized for someone
doing work. Exactly one must claim each request -- neither leaves an endpoint
unlimited, both halve its real budget -- so the two gates are written as
complements and live together rather than beside the middleware assembly that
consumes them.
"""

import re
from collections.abc import Callable
from typing import Final

from litestar import Request
from litestar.datastructures import State
from litestar.middleware.rate_limit import get_remote_address

#: What the rate-limit middleware accepts as a throttle gate.
type TierGate = Callable[[Request[object, object, State]], bool]

#: RFC 7235 makes the scheme token case-insensitive.
_BEARER_SCHEME: Final[str] = "bearer "


def self_authenticated_paths(prefix: str) -> re.Pattern[str]:
    """The two self-authenticating routes, under the configured API prefix.

    Built from the prefix rather than matched loosely, and anchored exactly as
    the same routes' auth exclusions are in ``middleware_factory``. An
    unanchored pattern would also match a sibling such as ``/other/gateway``,
    handing the authenticated tier's far larger budget to any route that
    happens to end in the same segment.

    Args:
        prefix: The configured API prefix, e.g. ``/api/v1``.

    Returns:
        The compiled route pattern.
    """
    return re.compile(rf"^{re.escape(prefix)}/gateway(/|$)")


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


def bears_own_credential(
    request: Request[object, object, State], routes: re.Pattern[str]
) -> bool:
    """Report whether the request presents a per-run bearer on its own path.

    The LLM gateway and the credentialed-tool MCP server verify their own
    bearer inside the handler, which is why both are auth-excluded, so
    ``scope["user"]`` is never populated for them. They are not anonymous
    traffic though, and the anonymous tier's cap is sized for a stranger with
    an IP: an agent doing ordinary work spends it in seconds and the run dies
    on a 429 from its own control plane.

    Both halves are required. The path alone says only where a request was
    aimed, so reaching the URL with no credential at all would buy the
    authenticated tier's far larger budget for free. Presenting a bearer is
    what makes a caller a plausible agent rather than a stranger.

    Syntax is all this can check: verifying the signature is the handler's
    job and duplicating it here would put the signing key in the throttle
    path. A forged-but-well-formed header therefore still reaches the larger
    bucket, but it stays keyed by client IP (``scope["user"]`` is unset on
    these routes), so a stranger buys a bigger budget for one address and
    every request in it still fails the handler's verification.

    Args:
        request: The incoming request.
        routes: The self-authenticating routes, from
            :func:`self_authenticated_paths`.

    Returns:
        ``True`` when the request is on a self-authenticating route AND
        carries a well-formed bearer header.
    """
    if routes.search(request.scope["path"]) is None:
        return False
    header = request.headers.get("authorization", "")
    if not header.lower().startswith(_BEARER_SCHEME):
        return False
    return bool(header[len(_BEARER_SCHEME) :].strip())


def build_throttle_gates(prefix: str) -> tuple[TierGate, TierGate]:
    """Build the anonymous and authenticated throttle gates for *prefix*.

    Both close over one compiled route pattern, so the two gates cannot drift
    into disagreeing about which routes self-authenticate: a request either
    counts against the anonymous bucket or the per-user one, and a split
    opinion would leave it in both or neither.

    The auth middleware runs before the rate-limit middleware (see middleware
    order at the bottom of ``build_middleware``), so ``scope["user"]`` is
    authoritatively populated by the time either gate runs: the real
    ``AuthenticatedUser`` after JWT/API-key verification, or ``None`` for
    auth-excluded paths (``/auth/login``, ``/auth/setup`` etc.) which the auth
    middleware skips. A forged session cookie cannot bypass this: if the JWT
    did not verify, auth either raised 401 before this point or left ``user``
    unset.

    Args:
        prefix: The configured API prefix, e.g. ``/api/v1``.

    Returns:
        The anonymous gate and the authenticated gate, in that order.
    """
    routes = self_authenticated_paths(prefix)

    def throttle_when_anonymous(request: Request[object, object, State]) -> bool:
        """Report whether the request counts against the anonymous bucket.

        Returns:
            ``True`` for anonymous traffic, ``False`` when the per-user tier
            should handle it instead.
        """
        if bears_own_credential(request, routes):
            return False
        return request.scope.get("user") is None

    def throttle_when_authenticated(request: Request[object, object, State]) -> bool:
        """Report whether the request counts against the per-user bucket.

        Returns:
            ``True`` when the per-user tier owns the request.
        """
        if bears_own_credential(request, routes):
            return True
        return request.scope.get("user") is not None

    return throttle_when_anonymous, throttle_when_authenticated
