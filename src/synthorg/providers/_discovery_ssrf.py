"""SSRF validation for provider model-discovery URLs.

Sibling of :mod:`synthorg.providers.discovery`, which stays focused on
the fetch / parse / enrich pipeline and delegates URL safety here:
scheme allow-listing, blocked private/reserved network ranges, DNS
resolution with rebinding-safe IP pinning.
"""

import asyncio
import ipaddress
import socket
from typing import Final, NamedTuple
from urllib.parse import urlparse, urlunparse

_ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

# Private, loopback, link-local, and reserved networks.
_BLOCKED_NETWORKS: Final[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = (
    ipaddress.IPv4Network("0.0.0.0/8"),
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("100.64.0.0/10"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.0.0.0/24"),
    ipaddress.IPv4Network("192.0.2.0/24"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv6Network("::/128"),
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
)


class SsrfCheckResult(NamedTuple):
    """Result of SSRF URL validation.

    Attributes:
        error: Error message if the URL is unsafe, or None if safe.
        pinned_ip: Resolved IP to connect to, preventing DNS rebinding
            between validation and the actual HTTP request.
    """

    error: str | None
    pinned_ip: str | None


async def validate_discovery_url(url: str) -> SsrfCheckResult:
    """Validate a URL for SSRF safety before making a discovery request.

    Allows http/https schemes only and blocks private/reserved IP
    addresses -- both literal IPs in the URL and resolved addresses
    for hostnames (DNS rebinding protection).  Hostnames like
    ``localhost`` are resolved via ``socket.getaddrinfo`` (offloaded
    to a thread executor to avoid blocking the event loop) and checked
    against the same blocked-network list.

    On success, returns the resolved IP so the caller can pin the
    connection to that address (preventing DNS rebinding between
    validation and the actual HTTP request).

    Args:
        url: URL to validate.

    Returns:
        Check result with error message or pinned IP.
    """
    parsed = urlparse(url)

    if parsed.scheme not in _ALLOWED_SCHEMES:
        return SsrfCheckResult(
            f"scheme {parsed.scheme!r} not allowed; use http or https",
            None,
        )

    hostname = parsed.hostname
    if not hostname:
        return SsrfCheckResult("URL has no hostname", None)

    return await _check_blocked_address(hostname)


async def _check_blocked_address(hostname: str) -> SsrfCheckResult:
    """Check whether a hostname resolves to a blocked network range.

    Handles both literal IPs and DNS names.  DNS resolution is
    offloaded to a thread executor to avoid blocking the event loop.

    Args:
        hostname: Hostname or IP address string.

    Returns:
        Check result with error or the safe resolved IP.
    """
    # Fast path: literal IP address (no I/O).
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        pass  # Not a literal IP -- resolve via DNS below.
    else:
        return _check_ip_blocked(addr, hostname)

    # Resolve hostname and check its first resolvable address.
    return await asyncio.to_thread(_check_resolved_hostname, hostname)


def _check_ip_blocked(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    label: str,
) -> SsrfCheckResult:
    """Check a single IP against blocked networks.

    Args:
        addr: IP address to check.
        label: Display label for error messages.

    Returns:
        Check result with error or the safe IP string.
    """
    # Unwrap an IPv4-mapped IPv6 address (e.g. ``::ffff:127.0.0.1``) before
    # the blocklist check below: Python's ``in`` on an ``IPv4Network``
    # returns False for an address still in IPv6 form (version mismatch),
    # so without unwrapping this loopback/private address would match
    # none of the IPv4Network entries below and bypass the blocklist.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    for network in _BLOCKED_NETWORKS:
        if addr in network:
            return SsrfCheckResult(
                f"address {label!r} is in a blocked network range",
                None,
            )
    return SsrfCheckResult(None, str(addr))


def _check_resolved_hostname(hostname: str) -> SsrfCheckResult:
    """Resolve a hostname and check its first resolvable address.

    Stops at the first entry ``getaddrinfo`` returns that parses as an
    IP (blocked or safe); a second DNS record is never inspected. Not a
    rebinding gap: the caller pins the outgoing connection to the exact
    address this returns via ``build_pinned_url``, so a resolver that
    later returns a different address is never reached.

    Args:
        hostname: DNS hostname to resolve.

    Returns:
        Check result with error or the first safe resolved IP.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return SsrfCheckResult(
            f"hostname {hostname!r} could not be resolved",
            None,
        )

    for _, _, _, _, sockaddr in infos:
        try:
            addr = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        result = _check_ip_blocked(addr, hostname)
        if result.error is not None:
            return SsrfCheckResult(
                f"hostname {hostname!r} resolves to {sockaddr[0]!r} in a blocked range",
                None,
            )
        # First safe address -- pin to it.
        return SsrfCheckResult(None, str(addr))

    return SsrfCheckResult(f"hostname {hostname!r} has no resolvable addresses", None)


def build_pinned_url(
    original_url: str,
    pinned_ip: str,
) -> tuple[str, str]:
    """Build a URL with hostname replaced by a resolved IP.

    Args:
        original_url: Original URL with hostname.
        pinned_ip: Resolved IP address to connect to.

    Returns:
        Tuple of (pinned_url, original_hostname) for Host header.
    """
    parsed = urlparse(original_url)
    original_host = parsed.hostname or ""
    port = parsed.port
    # IPv6 literal must be bracketed in URLs.
    ip_part = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    pinned_netloc = f"{ip_part}:{port}" if port else ip_part
    pinned_url = urlunparse(parsed._replace(netloc=pinned_netloc))
    return pinned_url, original_host
