"""Git clone URL validation -- SSRF prevention with DNS resolution.

Validates clone URLs against allowed schemes and performs hostname/IP
validation to prevent Server-Side Request Forgery (SSRF) attacks via
``git clone``.  All resolved IPs must be public; private, loopback,
link-local, and reserved addresses are blocked by default.  A
configurable hostname allowlist lets legitimate internal Git servers
bypass the private-IP check.

TOCTOU DNS rebinding is mitigated by two complementary strategies:

* **HTTPS URLs** -- the validated IPs are returned to the caller so
  it can pin ``git clone`` via ``-c http.curloptResolve`` (requires
  git >= 2.37.0, which exposes libcurl's ``CURLOPT_RESOLVE``; the
  sandbox container ships git 2.39+ so no runtime check is needed).
  This fully closes the TOCTOU gap for HTTPS.
* **SSH / SCP-like URLs** -- a second DNS resolution is performed
  immediately before execution and compared against the first; if new
  IPs appear that were not in the validated set the clone is blocked.
  Note: a residual TOCTOU window (~microseconds) exists between the
  second resolve and ``git clone`` execution; this is irreducible
  without OS-level DNS pinning.

Both mitigations can be disabled via
``GitCloneNetworkPolicy(dns_rebinding_mitigation=False)`` for
environments where DNS results legitimately vary between resolves
(CDN, geo-DNS, etc.).  Disabling ``block_private_ips`` also
implicitly disables TOCTOU mitigation (no IPs are resolved).
For defense-in-depth, combine with network-level egress controls
(firewall, HTTP CONNECT proxy).  See the sandbox design page for
planned network isolation.
"""

import ipaddress
import re
from typing import Final, Self
from urllib.parse import urlparse

import idna
from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.collections import dedupe_preserving_order
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.git import (
    GIT_CLONE_DNS_FAILED,
    GIT_CLONE_SSRF_BLOCKED,
    GIT_CLONE_SSRF_DISABLED,
)
from synthorg.tools._git_dns import is_blocked_clone_ip, resolve_and_check
from synthorg.tools.hostname_idna import canonical_hostname, describe_idna_failure

_CONTROL_CHAR_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────

ALLOWED_CLONE_SCHEMES: Final[tuple[str, ...]] = (
    "https://",
    "ssh://",
)

# Matches scheme://userinfo@host patterns in clone URLs.
_CREDENTIAL_RE: Final[re.Pattern[str]] = re.compile(r"(\w+://)[^@/]+@")


# ── Network policy model ─────────────────────────────────────────


class GitCloneNetworkPolicy(BaseModel):
    """Network policy for git clone SSRF prevention.

    Controls which hosts are allowed as clone targets.  By default,
    all public hosts are permitted while private, loopback, and
    link-local addresses are blocked.  Entries in
    ``hostname_allowlist`` bypass the private-IP check for legitimate
    internal Git servers.

    Allowlist entries are normalized to lowercase and deduplicated
    at construction time.

    Attributes:
        hostname_allowlist: Hostnames that bypass the private-IP
            check.  Stored lowercase after construction.
        block_private_ips: Master switch for private IP blocking.
            When ``False``, **all** hosts are allowed regardless
            of IP -- use only in development.
        dns_resolution_timeout: Timeout in seconds for each async
            DNS resolution.  For SSH/SCP URLs with
            ``dns_rebinding_mitigation=True``, up to two DNS lookups
            are performed (initial + consistency check), so the
            worst-case wall time is ``2 * dns_resolution_timeout``.
        dns_rebinding_mitigation: Enable TOCTOU DNS rebinding
            mitigation.  When ``True`` (default), HTTPS clones use
            ``http.curloptResolve`` to pin git to validated IPs,
            and SSH/SCP clones double-resolve to detect IP changes.
            Disable for hosts behind CDNs or geo-DNS where resolved
            IPs legitimately vary between queries.
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
    dns_rebinding_mitigation: bool = Field(
        default=True,
        description=(
            "Enable TOCTOU DNS rebinding mitigation "
            "(curloptResolve for HTTPS, double-resolve for SSH/SCP)"
        ),
    )

    @model_validator(mode="after")
    def _normalize_allowlist(self) -> Self:
        """Lowercase, canonicalise and deduplicate allowlist entries.

        Canonicalising matches what :func:`validate_clone_url` does to the
        request side, so an operator's U-label entry keeps matching once the
        clone target resolves to its A-label. Both SSRF paths answer the
        same question the same way; the alternative is one of them quietly
        comparing a different spelling from the other.

        Returns:
            Result of type ``Self``.
        """
        normalized = dedupe_preserving_order(
            canonical_hostname(normalize_ascii_lowercase(h))
            for h in self.hostname_allowlist
        )
        if normalized != self.hostname_allowlist:
            object.__setattr__(self, "hostname_allowlist", normalized)
        return self


# ── Validation result model ──────────────────────────────────────


class DnsValidationOk(BaseModel):
    """Successful DNS validation result with resolved addresses.

    Carries validated IP addresses so the caller can pin DNS
    resolution and close the TOCTOU gap between validation and
    ``git clone`` execution.

    Attributes:
        hostname: The normalized (lowercase) hostname that was
            resolved.
        port: Explicit port from the URL, or scheme default (443
            for HTTPS).  ``None`` for non-HTTPS URLs (SSH/SCP).
        resolved_ips: Deduplicated resolved IP addresses.  Empty
            for literal IPs, allowlisted hosts, disabled blocking,
            or when ``dns_rebinding_mitigation`` is off.
        is_https: Whether the URL uses HTTPS transport (eligible
            for ``http.curloptResolve`` pinning).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    hostname: NotBlankStr
    port: int | None = Field(default=None, gt=0, le=65535)
    resolved_ips: tuple[str, ...] = ()
    is_https: bool = False


# ── Helpers ──────────────────────────────────────────────────────


def _extract_hostname(url: str) -> str | None:
    """Extract the hostname from a clone URL.

    Supports:
    - Standard URLs: ``https://host/path``,
      ``ssh://user@host:port/path``
    - SCP-like syntax: ``user@host:path``
    - IPv6 literals: ``https://[::1]/path``

    Args:
        url: Repository URL string.

    Returns:
        The extracted hostname, or ``None`` if unparseable.
    """
    # Standard URL schemes
    if "://" in url:
        parsed = urlparse(url)
        hostname = parsed.hostname  # strips brackets from IPv6
        return hostname or None

    # SCP-like syntax: user@host:path
    if "@" in url and ":" in url:
        at_idx = url.index("@")
        rest = url[at_idx + 1 :]

        # IPv6 literal in brackets: git@[::1]:path
        if rest.startswith("["):
            bracket_end = rest.find("]")
            if bracket_end == -1:
                return None
            hostname = rest[1:bracket_end]
            return hostname or None

        colon_idx = rest.find(":")
        if colon_idx == -1:
            return None
        hostname = rest[:colon_idx]
        return hostname or None

    return None


def is_allowed_clone_scheme(url: str) -> bool:
    """Check if a clone URL uses an allowed remote scheme.

    Allows standard remote schemes and SCP-like syntax.  Rejects
    ``file://``, ``ext::``, bare local paths, and URLs starting with
    ``-`` (flag injection).

    Args:
        url: Repository URL string to validate.

    Returns:
        ``True`` if the URL scheme is allowed.
    """
    if url.startswith("-"):
        return False
    if any(url.startswith(scheme) for scheme in ALLOWED_CLONE_SCHEMES):
        return True
    # SCP-like syntax: user@host:path (e.g. git@github.com:user/repo.git).
    # Must have @ and : but NOT :// (rejects URLs that should match a
    # scheme above).  Bracketed IPv6 literals (git@[::1]:path) are
    # allowed; unbracketed :: is rejected (catches ext:: protocol).
    if "://" in url or "@" not in url or ":" not in url:
        return False
    _, rest = url.split("@", 1)
    if rest.startswith("["):
        bracket_end = rest.find("]")
        return bracket_end > 0 and rest[bracket_end + 1 : bracket_end + 2] == ":"
    host, _sep, _path = rest.partition(":")
    return bool(host) and "::" not in host


def build_curl_resolve_value(
    hostname: str,
    port: int,
    ips: tuple[str, ...],
) -> str:
    """Build a ``http.curloptResolve`` config value.

    Format: ``host:port:addr1,addr2,...``

    IPv6 addresses are wrapped in brackets per libcurl convention
    (e.g. ``[2607:f8b0::200e]``).

    Args:
        hostname: Hostname to pin.
        port: Port number (e.g. 443 for default HTTPS).
        ips: Non-empty tuple of validated IP addresses to pin to.

    Returns:
        The curloptResolve config string.

    Raises:
        ValueError: If *ips* is empty.
    """
    if not ips:
        logger.warning(
            GIT_CLONE_DNS_FAILED,
            hostname=hostname,
            reason="empty_ips_for_curl_resolve",
        )
        msg = "ips must not be empty"
        raise ValueError(msg)
    formatted = ",".join(f"[{ip}]" if ":" in ip else ip for ip in ips)
    return f"{hostname}:{port}:{formatted}"


# ── Main validator ───────────────────────────────────────────────


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


async def validate_clone_url_host(
    url: str,
    policy: GitCloneNetworkPolicy,
) -> str | DnsValidationOk:
    """Validate that a clone URL host is not private or internal.

    Performs DNS resolution to detect hosts resolving to private IPs
    (DNS rebinding prevention).  **All** resolved addresses must be
    public for the URL to be allowed.  Fails closed on DNS errors.

    On success, returns a ``DnsValidationOk`` carrying the resolved
    IPs so the caller can apply TOCTOU mitigation (curloptResolve
    pinning for HTTPS, double-resolve for SSH/SCP).

    Args:
        url: Repository URL string to validate.
        policy: Network policy controlling allowlist and blocking.

    Returns:
        An error message string if the host is blocked, or a
        ``DnsValidationOk`` on success.
    """
    hostname = _extract_hostname(url)
    if not hostname:
        redacted = _CREDENTIAL_RE.sub(r"\1***@", url)
        logger.warning(
            GIT_CLONE_SSRF_BLOCKED,
            url=redacted,
            reason="hostname_extraction_failed",
        )
        return f"Could not extract hostname from clone URL: {redacted!r}"

    normalized = normalize_ascii_lowercase(hostname)

    # Reject hostnames with control characters (defense-in-depth
    # against injection in curloptResolve values).
    if _CONTROL_CHAR_RE.search(normalized):
        logger.warning(
            GIT_CLONE_SSRF_BLOCKED,
            hostname=repr(normalized),
            reason="hostname_contains_control_characters",
        )
        return f"Clone URL hostname contains invalid characters: {normalized!r}"

    try:
        normalized = canonical_hostname(normalized)
    except idna.IDNAError as exc:
        failure = describe_idna_failure(exc)
        logger.warning(
            GIT_CLONE_SSRF_BLOCKED,
            hostname=normalized,
            reason="idna_invalid_hostname",
            idna_failure=failure,
        )
        return (
            "Clone URL hostname is not a valid internationalised "
            f"domain name: {failure}"
        )

    is_https = url.startswith("https://")
    port: int | None = None
    if is_https:
        try:
            raw_port = urlparse(url).port
        except ValueError:
            logger.warning(
                GIT_CLONE_SSRF_BLOCKED,
                hostname=normalized,
                reason="malformed_port",
            )
            return f"Invalid port in clone URL: {url!r}"
        if raw_port is not None and raw_port <= 0:
            logger.warning(
                GIT_CLONE_SSRF_BLOCKED,
                hostname=normalized,
                port=raw_port,
                reason="invalid_port",
            )
            return f"Invalid port in clone URL: {raw_port!r}"
        port = raw_port or 443

    # Allowlist bypass (pre-normalized to lowercase at construction)
    if normalized in policy.hostname_allowlist:
        return _ok(normalized, port, is_https=is_https)

    # Master switch
    if not policy.block_private_ips:
        logger.warning(
            GIT_CLONE_SSRF_DISABLED,
            hostname=normalized,
            reason="block_private_ips_disabled",
        )
        return _ok(normalized, port, is_https=is_https)

    # Literal IP -- no DNS needed, no TOCTOU gap
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass  # Not a literal IP, resolve below
    else:
        if is_blocked_clone_ip(normalized):
            logger.warning(
                GIT_CLONE_SSRF_BLOCKED,
                hostname=normalized,
                reason="literal_private_ip",
            )
            return f"Clone URL host {normalized!r} is a blocked private/reserved IP"
        return _ok(normalized, port, is_https=is_https)

    # DNS resolution + IP check
    result = await resolve_and_check(normalized, policy.dns_resolution_timeout)
    if isinstance(result, str):
        return result

    resolved_ips = result if policy.dns_rebinding_mitigation else ()
    return _ok(normalized, port, is_https=is_https, resolved_ips=resolved_ips)
