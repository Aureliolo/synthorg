# module-kind: integration
"""Model auto-discovery for LLM providers.

Two capabilities:

1. Auto-discovery when a preset is created with no explicit model list
   (e.g. Ollama, LM Studio, vLLM).
2. On-demand discovery for existing providers via the
   ``POST /{name}/discover-models`` endpoint.

URL probing (candidate URL probing for presets) lives in
:mod:`synthorg.providers.probing`.
"""

import asyncio
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable
from typing import Final, NamedTuple
from urllib.parse import urlparse, urlunparse

import httpx
from pydantic import JsonValue

from synthorg.config.schema import ProviderModelConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_DISCOVERY_FAILED,
    PROVIDER_DISCOVERY_SSRF_BYPASSED,
    PROVIDER_MODELS_DISCOVERED,
)
from synthorg.providers.probing import (
    _parse_ollama_models,
    _parse_standard_models,
)
from synthorg.providers.url_utils import redact_url as _redact_url

logger = get_logger(__name__)

_DISCOVERY_TIMEOUT_SECONDS: Final[float] = 10.0

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


class _SsrfCheckResult(NamedTuple):
    """Result of SSRF URL validation.

    Attributes:
        error: Error message if the URL is unsafe, or None if safe.
        pinned_ip: Resolved IP to connect to, preventing DNS rebinding
            between validation and the actual HTTP request.
    """

    error: str | None
    pinned_ip: str | None


async def _validate_discovery_url(url: str) -> _SsrfCheckResult:
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
        return _SsrfCheckResult(
            f"scheme {parsed.scheme!r} not allowed; use http or https",
            None,
        )

    hostname = parsed.hostname
    if not hostname:
        return _SsrfCheckResult("URL has no hostname", None)

    return await _check_blocked_address(hostname)


async def _check_blocked_address(hostname: str) -> _SsrfCheckResult:
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

    # Resolve hostname and check all returned addresses.
    return await asyncio.to_thread(_check_resolved_hostname, hostname)


def _check_ip_blocked(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
    label: str,
) -> _SsrfCheckResult:
    """Check a single IP against blocked networks.

    Args:
        addr: IP address to check.
        label: Display label for error messages.

    Returns:
        Check result with error or the safe IP string.
    """
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    for network in _BLOCKED_NETWORKS:
        if addr in network:
            return _SsrfCheckResult(
                f"address {label!r} is in a blocked network range",
                None,
            )
    return _SsrfCheckResult(None, str(addr))


def _check_resolved_hostname(hostname: str) -> _SsrfCheckResult:
    """Resolve a hostname and check all addresses against blocked networks.

    Args:
        hostname: DNS hostname to resolve.

    Returns:
        Check result with error or the first safe resolved IP.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return _SsrfCheckResult(
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
            return _SsrfCheckResult(
                f"hostname {hostname!r} resolves to {sockaddr[0]!r} in a blocked range",
                None,
            )
        # First safe address -- pin to it.
        return _SsrfCheckResult(None, str(addr))

    return _SsrfCheckResult(f"hostname {hostname!r} has no resolvable addresses", None)


async def discover_models(
    base_url: str,
    preset_name: str | None = None,
    *,
    headers: dict[str, str] | None = None,
    trust_url: bool = False,
) -> tuple[ProviderModelConfig, ...]:
    """Discover available models from a provider endpoint.

    For Ollama presets, queries ``GET {base_url}/api/tags``.
    For standard-API providers (LM Studio, vLLM, or unknown),
    queries ``GET {base_url}/models``.

    Args:
        base_url: Provider base URL (e.g. ``http://localhost:11434``
            for Ollama, ``http://localhost:1234/v1`` for LM Studio).
        preset_name: Preset identifier hint for endpoint selection.
        headers: Optional auth headers to include in the request.
        trust_url: When True, skip SSRF validation. Use only when
            the URL originates from a trusted source (e.g. a preset's
            ``candidate_urls`` or admin-entered during setup).

    Returns:
        Tuple of discovered model configs, or empty tuple on failure.
    """
    # ``ollama`` (local) and ``ollama-cloud`` (hosted) both speak the
    # native Ollama listing protocol (``GET /api/tags``); the hosted
    # variant only differs by base URL with the same payload shape.
    # ``_discover_standard_api`` calls ``GET {base}/models``, which
    # Ollama does not expose; routing the cloud preset there would
    # treat the 404 as an empty model list, and an empty list paired
    # with ``replace_existing=true`` deletes every persisted model.
    if preset_name in {"ollama", "ollama-cloud"}:
        return await _discover_ollama(
            base_url,
            headers=headers,
            trust_url=trust_url,
        )
    return await _discover_standard_api(
        base_url,
        preset_name,
        headers=headers,
        trust_url=trust_url,
    )


async def _discover_ollama(
    base_url: str,
    *,
    headers: dict[str, str] | None = None,
    trust_url: bool = False,
) -> tuple[ProviderModelConfig, ...]:
    """Discover models from Ollama's ``/api/tags`` endpoint.

    Args:
        base_url: Ollama server URL.
        headers: Optional auth headers.
        trust_url: Skip SSRF validation when True.

    Returns:
        Discovered models, or empty tuple on failure.
    """
    url = f"{base_url.rstrip('/')}/api/tags"
    data = await _fetch_json(url, "ollama", headers=headers, trust_url=trust_url)
    if data is None:
        return ()
    return _parse_and_log("ollama", url, data, _parse_ollama_models)


async def _discover_standard_api(
    base_url: str,
    preset_name: str | None,
    *,
    headers: dict[str, str] | None = None,
    trust_url: bool = False,
) -> tuple[ProviderModelConfig, ...]:
    """Discover models from a standard ``/models`` endpoint.

    Used for LM Studio, vLLM, and unknown providers that expose
    an ``/models`` listing endpoint.

    Args:
        base_url: Provider base URL.
        preset_name: Preset name for logging context.
        headers: Optional auth headers.
        trust_url: Skip SSRF validation when True.

    Returns:
        Discovered models, or empty tuple on failure.
    """
    url = f"{base_url.rstrip('/')}/models"
    data = await _fetch_json(
        url,
        preset_name,
        headers=headers,
        trust_url=trust_url,
    )
    if data is None:
        return ()
    return _parse_and_log(preset_name, url, data, _parse_standard_models)


def _parse_and_log(
    preset_name: str | None,
    url: str,
    data: dict[str, JsonValue],
    parse_fn: Callable[[dict[str, JsonValue]], tuple[ProviderModelConfig, ...] | None],
) -> tuple[ProviderModelConfig, ...]:
    """Parse a model-listing response and log skip counts.

    Delegates to the provided ``parse_fn`` (from probing.py) and
    adds skip-counting and structured logging around the result.

    Args:
        preset_name: Preset name for logging context.
        url: URL that was fetched (for logging).
        data: Parsed JSON response body.
        parse_fn: Parser function returning a tuple of
            ProviderModelConfig or None.

    Returns:
        Tuple of discovered model configs, or empty tuple.
    """
    models = parse_fn(data)
    if models is None:
        logger.warning(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="unexpected_response_structure",
            url=_redact_url(url),
        )
        return ()

    # Determine skip count from the raw list.
    raw_key = "models" if parse_fn is _parse_ollama_models else "data"
    raw_value = data.get(raw_key, [])
    raw_entries: list[JsonValue] = raw_value if isinstance(raw_value, list) else []
    skipped = len(raw_entries) - len(models)
    _log_skip_counts(preset_name, raw_entries, skipped, len(models))

    # Only log success when at least some models were parsed. If all
    # entries were malformed, _log_skip_counts already logged a warning.
    if models:
        logger.info(
            PROVIDER_MODELS_DISCOVERED,
            preset=preset_name,
            model_count=len(models),
        )
    return models


def _log_skip_counts(
    preset_name: str | None,
    raw_entries: list[JsonValue],
    skipped: int,
    model_count: int,
) -> None:
    """Log diagnostic info about malformed entries.

    Args:
        preset_name: Preset name for logging context.
        raw_entries: Raw list of model entries.
        skipped: Number of entries that were skipped.
        model_count: Number of valid models parsed.
    """
    if skipped and not model_count:
        logger.warning(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="all_entries_malformed",
            total_entries=len(raw_entries),
            skipped=skipped,
        )
    elif skipped:
        logger.debug(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="some_entries_malformed",
            skipped=skipped,
        )


def _build_pinned_url(
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


async def _fetch_json_trusted(
    url: str,
    preset_name: str | None,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, JsonValue] | None:
    """Fetch JSON from a trusted URL without SSRF validation.

    Used for URLs that originate from preset ``candidate_urls`` or
    were admin-entered during setup.  Local providers like Ollama
    use localhost/private IPs by design, which SSRF validation would
    block.  No IP pinning or Host-header rewriting is performed
    because the URL is used verbatim.

    Args:
        url: Full URL to fetch.
        preset_name: Preset name for logging context.
        headers: Optional auth headers to include.

    Returns:
        Parsed JSON dict, or ``None`` on any failure.
    """
    safe_url = _redact_url(url)
    logger.warning(
        PROVIDER_DISCOVERY_SSRF_BYPASSED,
        preset=preset_name,
        url=safe_url,
    )
    return await _safe_fetch(
        _do_fetch_json(url, headers, preset_name=preset_name),
        preset_name,
        safe_url,
    )


async def _fetch_json(
    url: str,
    preset_name: str | None,
    *,
    headers: dict[str, str] | None = None,
    trust_url: bool = False,
) -> dict[str, JsonValue] | None:
    """Fetch JSON from a URL with timeout and error handling.

    Validates the URL for SSRF safety before making the request
    unless ``trust_url`` is True (delegates to
    :func:`_fetch_json_trusted` for preset-originated URLs).

    Uses the resolved IP from validation to pin the connection,
    preventing DNS rebinding between validation and the HTTP request.

    Args:
        url: Full URL to fetch.
        preset_name: Preset name for logging context.
        headers: Optional auth headers to include.
        trust_url: When True, skip SSRF validation and IP pinning.

    Returns:
        Parsed JSON dict, or ``None`` on any failure.
    """
    if trust_url:
        return await _fetch_json_trusted(
            url,
            preset_name,
            headers=headers,
        )

    safe_url = _redact_url(url)
    pinned_url, original_host = await _validate_and_pin(
        url,
        preset_name,
        safe_url,
    )
    if pinned_url is None:
        return None

    return await _safe_fetch(
        _do_fetch_json(
            pinned_url,
            headers,
            host_header=original_host,
            preset_name=preset_name,
        ),
        preset_name,
        safe_url,
    )


async def _validate_and_pin(
    url: str,
    preset_name: str | None,
    safe_url: str,
) -> tuple[str | None, str]:
    """Validate a URL for SSRF and build a pinned URL.

    Args:
        url: Original URL to validate.
        preset_name: Preset name for logging context.
        safe_url: Redacted URL for log messages.

    Returns:
        Tuple of (pinned_url, original_host).  pinned_url is None
        if validation fails.
    """
    check = await _validate_discovery_url(url)
    if check.error is not None:
        logger.warning(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="blocked_url",
            url=safe_url,
            detail=check.error,
        )
        return None, ""

    pinned_ip = check.pinned_ip
    if pinned_ip is None:
        # Defensive: should not happen when error is None.
        logger.error(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="ssrf_check_inconsistency",
            url=safe_url,
            detail="SSRF check passed but returned no pinned IP",
        )
        return None, ""

    pinned_url, original_host = _build_pinned_url(url, pinned_ip)
    return pinned_url, original_host


async def _safe_fetch(
    coro: Awaitable[dict[str, JsonValue] | None],
    preset_name: str | None,
    safe_url: str,
) -> dict[str, JsonValue] | None:
    """Await a fetch coroutine with unified exception handling.

    Wraps the common try/except pattern shared by both trusted and
    SSRF-validated fetch paths.

    Args:
        coro: Awaitable returning a JSON dict or None.
        preset_name: Preset name for logging context.
        safe_url: Redacted URL for log messages.

    Returns:
        Parsed JSON dict, or ``None`` on any failure.
    """
    try:
        return await coro
    except httpx.HTTPStatusError as exc:
        logger.warning(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="http_error",
            url=safe_url,
            status_code=exc.response.status_code,
        )
    except httpx.ConnectError:
        _log_fetch_failure(preset_name, "connection_refused", safe_url)
    except httpx.TimeoutException:
        _log_fetch_failure(preset_name, "timeout", safe_url)
    except json.JSONDecodeError:
        _log_fetch_failure(preset_name, "invalid_json_response", safe_url)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            PROVIDER_DISCOVERY_FAILED,
            preset=preset_name,
            reason="unexpected_error",
            url=safe_url,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    return None


async def _do_fetch_json(
    url: str,
    headers: dict[str, str] | None,
    *,
    host_header: str = "",
    preset_name: str | None = None,
) -> dict[str, JsonValue] | None:
    """Execute the HTTP GET and parse JSON response.

    Args:
        url: URL to fetch (may be IP-pinned).
        headers: Optional request headers.
        host_header: Original hostname for the Host header (when
            the URL has been rewritten with a pinned IP).
        preset_name: Preset name for logging context.

    Returns:
        Parsed JSON dict, or ``None`` for non-dict responses.
    """
    merged_headers: dict[str, str] = {**(headers or {})}
    if host_header:
        merged_headers["Host"] = host_header
    async with httpx.AsyncClient(
        timeout=_DISCOVERY_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as client:
        response = await client.get(url, headers=merged_headers)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            logger.warning(
                PROVIDER_DISCOVERY_FAILED,
                preset=preset_name,
                reason="unexpected_json_type",
                url=_redact_url(url),
            )
            return None
        return result


def _log_fetch_failure(
    preset_name: str | None,
    reason: str,
    safe_url: str,
) -> None:
    """Log a discovery fetch failure with a standard structure."""
    logger.warning(
        PROVIDER_DISCOVERY_FAILED,
        preset=preset_name,
        reason=reason,
        url=safe_url,
    )
