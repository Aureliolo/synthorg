"""Shared network validation infrastructure for SSRF prevention.

Provides IP blocklist checking, hostname extraction, async DNS
resolution, and a reusable ``NetworkPolicy`` model used by web tools,
database tools, and (indirectly via ``GitCloneNetworkPolicy``) git
tools.

The canonical SSRF blocklist covers all private, loopback, link-local,
reserved, test-net, multicast, and broadcast ranges for both IPv4 and
IPv6.  IPv6-mapped IPv4 addresses (``::ffff:x.x.x.x``) are unwrapped
before checking.  Unparseable IPs are blocked (fail-closed).
"""

import asyncio
import ipaddress
from collections.abc import Sequence
from typing import Final
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.collections import dedupe_preserving_order
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import compare_ci
from synthorg.core.types import NotBlankStr
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.web import (
    WEB_DNS_FAILED,
    WEB_SSRF_BLOCKED,
    WEB_SSRF_DISABLED,
)

logger = get_logger(__name__)

# ── Blocked network ranges ─────────────────────────────────────

BLOCKED_NETWORKS: Final[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = (
    # IPv4 -- loopback, private, link-local, reserved
    ipaddress.IPv4Network("0.0.0.0/8"),
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("100.64.0.0/10"),  # CGNAT (RFC 6598)
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.0.0.0/24"),  # IETF Protocol Assignments
    ipaddress.IPv4Network("192.0.2.0/24"),  # TEST-NET-1 (RFC 5737)
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("198.18.0.0/15"),  # Benchmarking (RFC 2544)
    ipaddress.IPv4Network("198.51.100.0/24"),  # TEST-NET-2 (RFC 5737)
    ipaddress.IPv4Network("203.0.113.0/24"),  # TEST-NET-3 (RFC 5737)
    ipaddress.IPv4Network("224.0.0.0/4"),  # Multicast (RFC 5771)
    ipaddress.IPv4Network("240.0.0.0/4"),  # Reserved (RFC 1112)
    ipaddress.IPv4Network("255.255.255.255/32"),  # Broadcast
    # IPv6 -- loopback, link-local, ULA, tunneling, multicast, reserved
    ipaddress.IPv6Network("::/128"),
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("64:ff9b::/96"),  # NAT64 (RFC 6052)
    ipaddress.IPv6Network("100::/64"),  # Discard (RFC 6666)
    ipaddress.IPv6Network("2001::/32"),  # Teredo (RFC 4380)
    ipaddress.IPv6Network("2001:db8::/32"),  # Documentation (RFC 3849)
    ipaddress.IPv6Network("2002::/16"),  # 6to4 (RFC 3056) -- encodes IPv4
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
    ipaddress.IPv6Network("ff00::/8"),  # Multicast (RFC 4291)
)


# ── Network policy model ───────────────────────────────────────


class NetworkPolicy(BaseModel):
    """Reusable network policy for SSRF prevention across tool categories.

    Controls which hosts are allowed as request targets.  By default,
    all public hosts are permitted while private, loopback, and
    link-local addresses are blocked.  Entries in
    ``hostname_allowlist`` bypass the private-IP check for legitimate
    internal services.

    Attributes:
        hostname_allowlist: Hostnames that bypass the private-IP check.
            Stored lowercase after construction.
        block_private_ips: Master switch for private IP blocking.
            When ``False``, **all** hosts are allowed regardless of
            IP -- use only in development.
        dns_resolution_timeout: Timeout in seconds for each async DNS
            resolution.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    hostname_allowlist: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Hostnames that bypass the private-IP check",
    )
    block_private_ips: bool = Field(
        default=True,
        description="Master switch for private IP blocking",
    )
    dns_resolution_timeout: float = Field(
        default=5.0,
        gt=0,
        le=30.0,
        description="Timeout in seconds for DNS resolution",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_allowlist(cls, data: object) -> object:
        """Lowercase and deduplicate allowlist entries before construction.

        Returns:
            Result of type ``object``.
        """
        if not isinstance(data, dict) or "hostname_allowlist" not in data:
            return data
        raw = data["hostname_allowlist"]
        if not isinstance(raw, tuple | list):
            return data
        normalized = dedupe_preserving_order(h.lower() for h in raw)
        return {**data, "hostname_allowlist": normalized}


# ── Validation result model ─────────────────────────────────────


class DnsValidationOk(BaseModel):
    """Successful DNS validation result with resolved addresses.

    Carries validated IP addresses so the caller can pin DNS
    resolution and close the TOCTOU gap.

    Attributes:
        hostname: The normalized (lowercase) hostname that was resolved.
        port: Explicit port from the URL, or scheme default (443 for
            HTTPS, 80 for HTTP).  ``None`` for non-HTTP URLs.
        resolved_ips: Deduplicated resolved IP addresses.  Empty for
            literal IPs, allowlisted hosts, or disabled blocking.
        is_https: Whether the URL uses HTTPS transport.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    hostname: NotBlankStr
    port: int | None = Field(default=None, gt=0, le=65535)
    resolved_ips: tuple[str, ...] = ()
    is_https: bool = False


# ── IP validation ──────────────────────────────────────────────


def is_blocked_ip(addr: str) -> bool:
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
            WEB_SSRF_BLOCKED,
            addr=addr,
            reason="unparseable_ip_blocked",
        )
        return True  # Unparseable -> blocked (fail-closed)

    # Unwrap IPv6-mapped IPv4 (::ffff:x.x.x.x)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    return any(ip in network for network in BLOCKED_NETWORKS)


# ── Cloud-metadata / link-local endpoints ──────────────────────

_METADATA_HOSTNAMES: Final[frozenset[str]] = frozenset({"metadata.google.internal"})
"""Hostnames that resolve to a cloud instance-metadata service."""

_LINK_LOCAL_NETWORKS: Final[
    tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
] = (
    ipaddress.IPv4Network("169.254.0.0/16"),  # incl. the 169.254.169.254 IMDS
    ipaddress.IPv6Network("fe80::/10"),
)


def is_cloud_metadata_host(host: str) -> bool:
    """Check whether *host* is a link-local or cloud-metadata endpoint.

    Deliberately narrower than :func:`is_blocked_ip`: loopback and
    private ranges are NOT flagged, so a sandboxed tool can still reach
    an app-under-test on ``localhost`` or a docker-network address while
    link-local metadata endpoints (``169.254.169.254``, ``fe80::``,
    ``metadata.google.internal``) are refused. Link-local space is never
    a legitimate app target, so blocking the whole ``169.254.0.0/16`` /
    ``fe80::/10`` range also covers the metadata IP without a brittle
    single-address allowlist.

    Args:
        host: Hostname or IP literal extracted from a URL.

    Returns:
        ``True`` when *host* is a metadata hostname or a link-local IP.
    """
    if host.lower() in _METADATA_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in network for network in _LINK_LOCAL_NETWORKS)


# ── Hostname extraction ────────────────────────────────────────


def extract_hostname(url: str) -> str | None:
    """Extract the hostname from a URL.

    Supports standard URL schemes (``https://host/path``) and
    IPv6 literals (``https://[::1]/path``).

    Args:
        url: URL string.

    Returns:
        The extracted hostname, or ``None`` if unparseable.
    """
    if "://" not in url:
        return None
    parsed = urlparse(url)
    hostname = parsed.hostname  # strips brackets from IPv6
    return hostname or None


# ── Scheme validation ──────────────────────────────────────────


_ALLOWED_HTTP_SCHEMES: Final[tuple[str, ...]] = ("http://", "https://")


def is_allowed_http_scheme(url: str) -> bool:
    """Check if a URL uses an allowed HTTP scheme.

    Allows ``http://`` and ``https://`` only.  Rejects ``file://``,
    ``ftp://``, ``gopher://``, bare paths, and URLs starting with
    ``-`` (flag injection).

    Args:
        url: URL string to validate.

    Returns:
        ``True`` if the URL scheme is allowed.
    """
    stripped = url.lstrip()
    if stripped.startswith("-"):
        return False
    url_lower = stripped.lower()
    return any(url_lower.startswith(scheme) for scheme in _ALLOWED_HTTP_SCHEMES)


# ── DNS resolution helpers ──────────────────────────────────────


def _dns_failure(
    hostname: str,
    reason: str,
    message: str,
    *,
    error_type: str | None = None,
    error: str | None = None,
) -> str:
    """Log a DNS resolution failure and return the error message.

    ``error_type`` / ``error`` are forwarded as structured fields when
    the caller has a sanitised exception description (the OSError
    branch of :func:`resolve_dns`); the timeout branch passes neither
    because the cause is the wait, not a backend failure.

    Returns:
        Result of type ``str``.
    """
    payload: dict[str, object] = {"hostname": hostname, "reason": reason}
    if error_type is not None:
        payload["error_type"] = error_type
    if error is not None:
        payload["error"] = error
    logger.warning(WEB_DNS_FAILED, **payload)
    return message


# A single ``socket.getaddrinfo`` entry; the last element is the sockaddr.
type _AddrInfo = tuple[
    object, object, object, object, tuple[str, int] | tuple[str, int, int, int]
]


async def resolve_dns(
    hostname: str,
    dns_timeout: float,
) -> str | Sequence[_AddrInfo]:
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
        safe_error = safe_error_description(exc)
        return _dns_failure(
            hostname,
            "dns_resolution_error",
            f"DNS resolution for {hostname!r} failed: {safe_error}",
            error_type=type(exc).__name__,
            error=safe_error,
        )
    except Exception as exc:
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            WEB_DNS_FAILED,
            exc,
            hostname=hostname,
            reason="unexpected_dns_resolution_error",
        )
        return f"DNS resolution for {hostname!r} failed: {safe_error_description(exc)}"

    if not results:
        return _dns_failure(
            hostname,
            "no_results",
            f"DNS resolution for {hostname!r} returned no results",
        )

    return results


def check_resolved_ips(
    hostname: str,
    results: Sequence[_AddrInfo],
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
        if is_blocked_ip(addr):
            logger.warning(
                WEB_SSRF_BLOCKED,
                hostname=hostname,
                resolved_ip=addr,
                reason="dns_resolves_to_private_ip",
            )
            return (
                f"URL host {hostname!r} resolves to blocked private/reserved IP {addr}"
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
    results = await resolve_dns(hostname, dns_timeout)
    if isinstance(results, str):
        return results
    return check_resolved_ips(hostname, results)


# ── Main validator ──────────────────────────────────────────────


def _ok(
    hostname: str,
    port: int | None,
    *,
    is_https: bool,
    resolved_ips: tuple[str, ...] = (),
) -> DnsValidationOk:
    """Construct a successful validation result.

    Returns:
        Result of type ``DnsValidationOk``.
    """
    return DnsValidationOk(
        hostname=hostname,
        port=port,
        resolved_ips=resolved_ips,
        is_https=is_https,
    )


def _resolve_url_port(url: str, normalized: str, *, is_https: bool) -> int | str:
    """Resolve the explicit or scheme-default port for a URL.

    Args:
        url: URL string being validated.
        normalized: Lowercased hostname (for diagnostic events).
        is_https: Whether the URL uses HTTPS transport.

    Returns:
        The resolved port integer, or an error message string when the
        port is malformed or non-positive.
    """
    try:
        raw_port = urlparse(url).port
    except ValueError:
        logger.warning(WEB_SSRF_BLOCKED, hostname=normalized, reason="malformed_port")
        return f"Invalid port in URL: {url!r}"
    if raw_port is not None and raw_port <= 0:
        logger.warning(
            WEB_SSRF_BLOCKED,
            hostname=normalized,
            port=raw_port,
            reason="invalid_port",
        )
        return f"Invalid port in URL: {raw_port!r}"
    if raw_port is not None:
        return raw_port
    return 443 if is_https else 80


async def _check_host_against_policy(
    normalized: str,
    port: int,
    *,
    is_https: bool,
    policy: NetworkPolicy,
) -> str | DnsValidationOk:
    """Apply the SSRF policy to an already-extracted hostname.

    Args:
        normalized: Lowercased hostname.
        port: Resolved port.
        is_https: Whether the URL uses HTTPS transport.
        policy: Network policy controlling allowlist and blocking.

    Returns:
        An error message string if the host is blocked, or a
        ``DnsValidationOk`` on success.
    """
    # Allowlist bypass -- still resolve DNS for IP pinning.
    if normalized in policy.hostname_allowlist:
        result = await resolve_and_check(normalized, policy.dns_resolution_timeout)
        if isinstance(result, tuple):
            resolved = result
        else:
            logger.warning(
                WEB_DNS_FAILED,
                hostname=normalized,
                reason=f"allowlisted_host_dns_failed: {result}",
            )
            resolved = ()
        return _ok(normalized, port, is_https=is_https, resolved_ips=resolved)

    # Master switch
    if not policy.block_private_ips:
        logger.warning(
            WEB_SSRF_DISABLED,
            hostname=normalized,
            reason="block_private_ips_disabled",
        )
        return _ok(normalized, port, is_https=is_https)

    # Literal IP -- no DNS needed
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass  # Not a literal IP, resolve below
    else:
        if is_blocked_ip(normalized):
            logger.warning(
                WEB_SSRF_BLOCKED,
                hostname=normalized,
                reason="literal_private_ip",
            )
            return f"URL host {normalized!r} is a blocked private/reserved IP"
        return _ok(normalized, port, is_https=is_https)

    # DNS resolution + IP check
    result = await resolve_and_check(normalized, policy.dns_resolution_timeout)
    if isinstance(result, str):
        return result

    return _ok(normalized, port, is_https=is_https, resolved_ips=result)


async def validate_url_host(
    url: str,
    policy: NetworkPolicy,
) -> str | DnsValidationOk:
    """Validate that a URL host is not private or internal.

    Performs DNS resolution to detect hosts resolving to private IPs
    (DNS rebinding prevention).  **All** resolved addresses must be
    public for the URL to be allowed.  Fails closed on DNS errors.

    On success, returns a ``DnsValidationOk`` carrying the resolved
    IPs so the caller can apply TOCTOU mitigation.

    Args:
        url: URL string to validate.
        policy: Network policy controlling allowlist and blocking.

    Returns:
        An error message string if the host is blocked, or a
        ``DnsValidationOk`` on success.
    """
    hostname = extract_hostname(url)
    if not hostname:
        logger.warning(
            WEB_SSRF_BLOCKED,
            url=url,
            reason="hostname_extraction_failed",
        )
        return f"Could not extract hostname from URL: {url!r}"

    normalized = hostname.lower()
    is_https = compare_ci(urlparse(url).scheme, "https")
    port = _resolve_url_port(url, normalized, is_https=is_https)
    if isinstance(port, str):
        return port
    return await _check_host_against_policy(
        normalized, port, is_https=is_https, policy=policy
    )
