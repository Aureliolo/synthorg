"""Shared subject-identity resolution for rate-limit guards.

Both the sliding-window ``per_op_rate_limit`` guard and the inflight
``PerOpConcurrencyMiddleware`` must bucket requests by the same
"real client" identifier -- otherwise a malicious caller could hit
the rate-limiter under user-keying but escape the concurrency cap by
spoofing the request shape, or vice versa.  Factoring the helpers out
of ``guard.py`` makes the contract explicit and keeps the two code
paths in lockstep.
"""

import ipaddress
from typing import Final, Literal

from litestar.connection import ASGIConnection
from litestar.datastructures import State

from synthorg.core.normalization import parse_comma_list_stripped
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_GUARD_DEGRADED_AUTH

logger = get_logger(__name__)

KeyPolicy = Literal["user", "ip", "user_or_ip"]

# AppState keys -- wired in ``api/app.py`` startup.
STATE_KEY_STORE: Final[str] = "per_op_rate_limit_store"
STATE_KEY_CONFIG: Final[str] = "per_op_rate_limit_config"
STATE_KEY_INFLIGHT_STORE: Final[str] = "per_op_inflight_store"
STATE_KEY_INFLIGHT_CONFIG: Final[str] = "per_op_inflight_config"
# Trusted-proxy raw set (frozenset[str]) kept on app state for
# diagnostic reads; the pre-parsed tuple below is what both per-op
# guards consult at request time.
STATE_KEY_TRUSTED_PROXIES: Final[str] = "per_op_trusted_proxies"
# Pre-parsed tuple of ``ip_network`` objects built once at startup
# from ``per_op_trusted_proxies``.  Parsing per-request added a
# measurable hot-path cost (each guarded mutation re-parsed the full
# set); caching the parsed tuple keeps the "real client IP" resolution
# constant-cost per request.
STATE_KEY_TRUSTED_NETWORKS: Final[str] = "per_op_trusted_networks"
TrustedNetworks = tuple[
    ipaddress.IPv4Network | ipaddress.IPv6Network,
    ...,
]
# Trusted-proxy-normalised client IP key -- stashed on the ASGI scope
# by any middleware that has already resolved the forwarded IP.  The
# helpers read this first and only walk X-Forwarded-For themselves when
# the immediate peer is in ``per_op_trusted_proxies``.
SCOPE_KEY_TRUSTED_IP: Final[str] = "trusted_client_ip"


def extract_subject_key(
    connection: ASGIConnection[object, object, object, State],
    policy: KeyPolicy,
    *,
    guard_name: str,
) -> str:
    """Resolve the subject identifier for a given key policy.

    Args:
        connection: The incoming request's ASGI connection.
        policy: Which identity to bucket on (``user``, ``ip``, or
            ``user_or_ip``).
        guard_name: Name of the caller (``"per_op_rate_limit"`` or
            ``"per_op_concurrency"``) for telemetry attribution when
            the ``user`` policy falls back to IP.

    Returns:
        A stable string identifier prefixed with ``"user:"`` or
        ``"ip:"`` so the two namespaces never collide.
    """
    user = connection.scope.get("user")
    user_id = getattr(user, "user_id", None) if user is not None else None
    if policy == "user":
        if user_id is None:
            # Authenticated request expected but no user populated.
            # Fall back to IP so anonymous calls still get throttled
            # rather than bypassing the limiter entirely, but log so
            # operators notice when auth middleware silently strips
            # the user claim.
            logger.warning(
                API_GUARD_DEGRADED_AUTH,
                guard=guard_name,
                note="user_key_missing_user_id_falling_back_to_ip",
            )
            return f"ip:{client_ip(connection)}"
        return f"user:{user_id}"
    if policy == "ip":
        return f"ip:{client_ip(connection)}"
    if user_id is not None:
        return f"user:{user_id}"
    return f"ip:{client_ip(connection)}"


def _peer_ip(connection: ASGIConnection[object, object, object, State]) -> str | None:
    """Return the immediate peer IP from the ASGI scope.

    Returns:
        The ``str`` value when present, ``None`` otherwise.
    """
    client = connection.scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        return str(client[0])
    return None


def client_ip(connection: ASGIConnection[object, object, object, State]) -> str:
    """Extract a proxy-aware best-effort client IP from the connection.

    Resolution order:

    1. ``scope["trusted_client_ip"]`` if a middleware already resolved it.
    2. If the immediate peer is in ``per_op_trusted_proxies`` (state),
       walk ``X-Forwarded-For`` right-to-left and return the first hop
       outside the trusted set.  This matches the global limiter's
       ``_build_unauth_identifier`` semantics so both tiers pick the
       same "real" client identifier.
    3. Otherwise return the immediate peer IP -- the raw
       ``X-Forwarded-For`` header is **never** trusted from untrusted
       peers (would let any caller spoof identities to bypass
       ip/user_or_ip throttles).

    ``"unknown"`` is returned when the connection has no client
    metadata at all (rare, typically test fixtures).

    Returns:
        Resulting string.
    """
    trusted = connection.scope.get(SCOPE_KEY_TRUSTED_IP)
    if isinstance(trusted, str) and trusted:
        return trusted
    peer = _peer_ip(connection)
    trusted_networks = _trusted_networks(connection)
    if peer is not None and _ip_in_networks(peer, trusted_networks):
        forwarded = connection.headers.get("x-forwarded-for", "")
        if forwarded:
            hops = parse_comma_list_stripped(forwarded)
            for hop in reversed(hops):
                if _ip_in_networks(hop, trusted_networks):
                    continue
                # Validate the hop is a real IP before returning it as a
                # subject key.  Otherwise a caller behind a trusted proxy
                # could send ``X-Forwarded-For: garbage, 10.0.0.1`` and
                # get bucketed under ``ip:garbage``, then rotate
                # arbitrary strings to evade both tiers of the guard.
                try:
                    ipaddress.ip_address(hop)
                except ValueError:
                    continue
                return hop
    return peer or "unknown"


def parse_trusted_networks(raw: frozenset[str]) -> TrustedNetworks:
    """Parse a ``frozenset[str]`` of proxy CIDRs into IP networks.

    Called once at startup by ``api/app.py`` to precompute the tuple
    stashed on app state; also used by tests that need to build the
    same shape without going through the full app lifecycle.  Malformed
    entries are skipped (logged by config validation at ingest time,
    not here) so a typo in one proxy CIDR does not disable the whole
    trusted-proxy set.

    Returns:
        ``TrustedNetworks`` instance.
    """
    parsed: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in raw:
        try:
            parsed.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return tuple(parsed)


def _trusted_networks(
    connection: ASGIConnection[object, object, object, State],
) -> TrustedNetworks:
    """Return the cached trusted-network tuple from app state.

    The tuple is built once at startup by ``api/app.py`` via
    :func:`parse_trusted_networks` and stashed under
    ``STATE_KEY_TRUSTED_NETWORKS``.  Falls back to parsing on the fly
    if the cache is not present (test fixtures that bypass full app
    startup) so the guard remains correct even with a minimal state.

    Returns:
        ``TrustedNetworks`` instance.
    """
    cached: TrustedNetworks | None = getattr(
        connection.app.state,
        STATE_KEY_TRUSTED_NETWORKS,
        None,
    )
    if cached is not None:
        return cached
    raw: frozenset[str] = getattr(
        connection.app.state,
        STATE_KEY_TRUSTED_PROXIES,
        frozenset(),
    )
    return parse_trusted_networks(raw)


def _ip_in_networks(
    ip_str: str,
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> bool:
    """Return True when ``ip_str`` is inside any of ``networks``.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    if not networks:
        return False
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(addr in net for net in networks)
