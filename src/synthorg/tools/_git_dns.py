# module-kind: code
"""DNS resolution and rebinding checks for the git clone SSRF guard.

Separate from :mod:`synthorg.tools.git_url_validator`, which decides what a
clone URL is allowed to name. This decides what its name currently resolves
to, and whether it still resolves to the same thing at the moment the clone
runs.
"""

import asyncio
import ipaddress
from collections.abc import Sequence
from typing import cast

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.git import (
    GIT_CLONE_DNS_FAILED,
    GIT_CLONE_DNS_REBINDING_DETECTED,
    GIT_CLONE_SSRF_BLOCKED,
)
from synthorg.tools.network_validator import BLOCKED_NETWORKS

logger = get_logger(__name__)


def is_blocked_clone_ip(addr: str) -> bool:
    """Check whether an IP address falls within a blocked network.

    Handles IPv6-mapped IPv4 addresses (e.g. ``::ffff:127.0.0.1``)
    by extracting the mapped IPv4 address for validation.

    Args:
        addr: IP address string to check.

    Returns:
        ``True`` if the address is in a blocked network range.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        logger.warning(
            GIT_CLONE_SSRF_BLOCKED,
            addr=addr,
            reason="unparseable_ip_blocked",
        )
        return True  # Unparseable -> blocked (fail-closed)

    # Unwrap IPv6-mapped IPv4 (::ffff:x.x.x.x)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    return any(ip in network for network in BLOCKED_NETWORKS)


type AddrInfo = tuple[
    object,
    object,
    object,
    object,
    tuple[str, int] | tuple[str, int, int, int],
]
"""A single ``socket.getaddrinfo`` entry; the last element is the sockaddr."""


def _dns_failure(hostname: str, reason: str, message: str) -> str:
    """Log a DNS resolution failure and return the error message.

    Returns:
        Result of type ``str``.
    """
    logger.warning(
        GIT_CLONE_DNS_FAILED,
        hostname=hostname,
        reason=reason,
    )
    return message


async def _resolve_dns(
    hostname: str,
    dns_timeout: float,
) -> str | Sequence[AddrInfo]:
    """Resolve *hostname* via async DNS.

    Args:
        hostname: Lowercase hostname to resolve.
        dns_timeout: DNS resolution timeout in seconds.

    Returns:
        An error message string on failure, or the raw
        ``getaddrinfo`` result list on success.
    """
    loop = asyncio.get_running_loop()
    try:
        results = await asyncio.wait_for(
            loop.getaddrinfo(hostname, None),
            timeout=dns_timeout,
        )
    except TimeoutError:
        return _dns_failure(
            hostname,
            "timeout",
            f"DNS resolution for {hostname!r} timed out",
        )
    except OSError as exc:
        return _dns_failure(
            hostname,
            "dns_resolution_error",
            f"DNS resolution for {hostname!r} failed: {safe_error_description(exc)}",
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        log_exception_redacted(
            logger, GIT_CLONE_DNS_FAILED, exc, hostname=hostname, reason="unexpected"
        )
        return f"DNS resolution for {hostname!r} failed: {safe_error_description(exc)}"

    if not results:
        return _dns_failure(
            hostname,
            "no_results",
            f"DNS resolution for {hostname!r} returned no results",
        )

    # getaddrinfo on a hostname yields only AF_INET/AF_INET6 entries, whose
    # sockaddr is an IP tuple; typeshed types the sockaddr more broadly (it
    # includes an AF_PACKET ``tuple[int, bytes]`` variant that name
    # resolution never produces), so the narrower domain type is asserted here.
    return cast("Sequence[AddrInfo]", results)


def check_resolved_ips(
    hostname: str,
    results: Sequence[AddrInfo],
) -> str | tuple[str, ...]:
    """Validate resolved IPs and return deduplicated public addresses.

    Args:
        hostname: Hostname that was resolved (for error messages).
        results: Raw ``getaddrinfo`` result tuples.

    Returns:
        A deduplicated tuple of validated public IP strings on
        success, or an error message string if any IP is blocked.
    """
    seen: dict[str, None] = {}
    for *_info, sockaddr in results:
        addr = sockaddr[0]
        if is_blocked_clone_ip(addr):
            logger.warning(
                GIT_CLONE_SSRF_BLOCKED,
                hostname=hostname,
                resolved_ip=addr,
                reason="dns_resolves_to_private_ip",
            )
            return (
                f"Clone URL host {hostname!r} resolves to "
                f"blocked private/reserved IP {addr}"
            )
        seen[addr] = None

    return tuple(seen)


async def resolve_and_check(
    hostname: str,
    dns_timeout: float,
) -> str | tuple[str, ...]:
    """Resolve *hostname* via DNS and check all IPs against blocklist.

    Args:
        hostname: Lowercase hostname to resolve.
        dns_timeout: DNS resolution timeout in seconds.

    Returns:
        A deduplicated tuple of validated public IP strings on
        success, or an error message string if any resolved IP is
        blocked or DNS fails.
    """
    results = await _resolve_dns(hostname, dns_timeout)
    if isinstance(results, str):
        return results
    return check_resolved_ips(hostname, results)


async def verify_dns_consistency(
    hostname: str,
    expected_ips: frozenset[str],
    dns_timeout: float,
) -> str | None:
    """Re-resolve *hostname* and verify consistency with prior result.

    Performs a second DNS resolution immediately before execution and
    checks two conditions:

    1. All re-resolved IPs must be public (primary SSRF defense).
    2. The re-resolved IP set must be a subset of *expected_ips*
       (detects DNS rebinding where IPs change between resolves).

    Args:
        hostname: Lowercase hostname to re-resolve.
        expected_ips: IP addresses from the initial validation.
        dns_timeout: DNS resolution timeout in seconds.

    Returns:
        An error message if rebinding is detected or any IP is
        blocked, or ``None`` if the resolution is consistent.
    """
    result = await resolve_and_check(hostname, dns_timeout)
    if isinstance(result, str):
        return result

    new_ips = frozenset(result)
    unexpected = new_ips - expected_ips
    if unexpected:
        logger.warning(
            GIT_CLONE_DNS_REBINDING_DETECTED,
            hostname=hostname,
            expected_ips=sorted(expected_ips),
            new_ips=sorted(new_ips),
            unexpected_ips=sorted(unexpected),
        )
        return (
            f"DNS rebinding detected for {hostname!r}: "
            f"re-resolved IPs {sorted(new_ips)} include addresses "
            f"not in validated set {sorted(expected_ips)}"
        )

    return None
