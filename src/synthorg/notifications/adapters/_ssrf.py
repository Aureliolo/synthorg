"""Shared SSRF pre-flight for outbound notification webhooks.

The ntfy and Slack sinks both POST to an operator-configured URL. A
static host check (exact-string loopback + literal-IP) does not catch a
DNS name that resolves to an internal address (e.g. a cloud metadata
endpoint at ``169.254.169.254`` behind a CNAME), so this module routes
both adapters through the hardened ``network_validator`` path used by the
other HTTP egress sites in the codebase: async DNS resolution, every
resolved IP checked against ``BLOCKED_NETWORKS``, and a pinned-IP
transport so DNS rebinding cannot redirect the live connect after
validation.
"""

import ipaddress
from typing import Final
from urllib.parse import urlparse

from synthorg.observability import get_logger
from synthorg.observability.events.notification import NOTIFICATION_SINK_CONFIG_INVALID
from synthorg.tools._dns_pinning import PinnedDnsTransport
from synthorg.tools.network_validator import (
    DnsValidationOk,
    NetworkPolicy,
    validate_url_host,
)

logger = get_logger(__name__)

_ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
_BLOCKED_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1", "::1"})


def validate_outbound_url_scheme(url: str, field: str) -> None:
    """Synchronous fast-fail check on scheme and literal-IP hosts.

    Runs at sink construction so an obviously-bad URL (non-HTTP scheme,
    literal loopback / private IP, exact ``localhost``) is rejected
    immediately. This is NOT the SSRF gate: a hostname that resolves to
    an internal address is only caught by the async
    :func:`resolve_outbound_target` pre-flight at start time.

    Raises:
        ValueError: When the scheme is not http(s) or the host is a
            literal loopback / private / link-local IP (or ``localhost``).
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        msg = f"{field} must use http or https scheme, got {parsed.scheme!r}"
        raise ValueError(msg)
    host = parsed.hostname or ""
    if host in _BLOCKED_HOSTS:
        msg = f"{field} must not target loopback address"
        raise ValueError(msg)
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return
    # IPv4-mapped IPv6 (``::ffff:127.0.0.1``) reports neither ``is_private``
    # nor ``is_loopback`` on the IPv6Address; unwrap to the embedded IPv4 so
    # the loopback/private/link-local check cannot be bypassed via the mapped
    # notation.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if addr.is_private or addr.is_link_local or addr.is_loopback:
        msg = f"{field} must not target private/internal IP"
        raise ValueError(msg)


def _is_literal_ip(host: str) -> bool:
    """Return ``True`` when ``host`` is a literal IP (no DNS to pin)."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


async def resolve_outbound_target(
    url: str,
    *,
    field: str,
    policy: NetworkPolicy,
) -> DnsValidationOk:
    """Async SSRF pre-flight: DNS-resolve + block internal targets.

    Returns the validated result (carrying the pinned IPs) so the caller
    can build a :func:`build_pinned_transport`. A hostname on the policy
    allowlist bypasses the block-private rule but still resolves so the
    connect can be pinned.

    Returns:
        The ``DnsValidationOk`` for an accepted target.

    Raises:
        ValueError: When the URL is rejected by the SSRF policy
            (non-HTTP scheme, unresolvable host, or any resolved IP in a
            blocked range), or when an allowlisted hostname could not be
            resolved to an IP and therefore cannot be pinned.
    """
    result = await validate_url_host(url, policy)
    if not isinstance(result, DnsValidationOk):
        # The isinstance is a success/error discriminant on the
        # validator's union return, not argument type-validation, so the
        # rejection is a value error, not a TypeError.
        msg = f"{field} rejected by SSRF policy: {result}"
        raise ValueError(msg)  # noqa: TRY004 -- value rejection, not a type mismatch
    if (
        policy.block_private_ips
        and not result.resolved_ips
        and not _is_literal_ip(result.hostname)
    ):
        # An allowlisted hostname whose DNS failed carries no pinned IP, so
        # the live connect would re-resolve at request time and reopen the
        # rebinding window. Fail closed rather than fall back to an unpinned
        # transport. Literal-IP targets legitimately have no resolved IPs and
        # need no pin, so they are exempt; when ``block_private_ips`` is off
        # the operator has disabled SSRF protection entirely, so pinning is
        # not expected and an empty result is allowed through.
        logger.warning(
            NOTIFICATION_SINK_CONFIG_INVALID,
            field=field,
            hostname=result.hostname,
            reason="allowlisted_host_dns_unresolved_no_pin",
        )
        msg = (
            f"{field}: allowlisted host {result.hostname!r} could not be "
            "resolved for DNS pinning"
        )
        raise ValueError(msg)
    return result


def build_pinned_transport(
    validation: DnsValidationOk,
) -> PinnedDnsTransport | None:
    """Build a DNS-pinned transport from a validated target.

    Pins the TCP connect to the first validated IP so a malicious DNS
    server cannot rebind the hostname between the pre-flight and the live
    request. Returns ``None`` for literal-IP targets where ``resolved_ips``
    is empty; the caller then omits the ``transport`` argument so
    ``httpx.AsyncClient`` uses its default transport, which connects
    straight to the literal IP (nothing to rebind).

    Returns:
        A ``PinnedDnsTransport`` when pinned IPs are available, else
        ``None``.
    """
    if validation.resolved_ips:
        return PinnedDnsTransport(
            hostname=validation.hostname,
            ip=validation.resolved_ips[0],
        )
    return None
