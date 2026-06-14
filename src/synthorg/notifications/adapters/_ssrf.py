"""Shared SSRF pre-flight for outbound notification webhooks.

The ntfy and Slack sinks both POST to an operator-configured URL. A
static host check (exact-string loopback + literal-IP) does not catch a
DNS name that resolves to an internal address (e.g. a cloud metadata
endpoint at ``169.254.169.254`` behind a CNAME), so this module routes
both adapters through the hardened ``network_validator`` path used by
every other HTTP egress in the codebase: async DNS resolution, every
resolved IP checked against ``BLOCKED_NETWORKS``, and a pinned-IP
transport so DNS rebinding cannot redirect the live connect after
validation.
"""

import ipaddress
from typing import Final
from urllib.parse import urlparse

import httpx

from synthorg.tools._dns_pinning import PinnedDnsTransport
from synthorg.tools.network_validator import (
    DnsValidationOk,
    NetworkPolicy,
    validate_url_host,
)

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
    if addr.is_private or addr.is_link_local or addr.is_loopback:
        msg = f"{field} must not target private/internal IP"
        raise ValueError(msg)


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
            blocked range).
    """
    result = await validate_url_host(url, policy)
    if not isinstance(result, DnsValidationOk):
        # The isinstance is a success/error discriminant on the
        # validator's union return, not argument type-validation, so the
        # rejection is a value error, not a TypeError.
        msg = f"{field} rejected by SSRF policy: {result}"
        raise ValueError(msg)  # noqa: TRY004 -- value rejection, not a type mismatch
    return result


def build_pinned_transport(
    validation: DnsValidationOk,
) -> httpx.AsyncBaseTransport | None:
    """Build a DNS-pinned transport from a validated target.

    Pins the TCP connect to the first validated IP so a malicious DNS
    server cannot rebind the hostname between the pre-flight and the live
    request. Returns ``None`` for literal-IP / allowlisted targets where
    ``resolved_ips`` is empty (the default transport is then used).

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
