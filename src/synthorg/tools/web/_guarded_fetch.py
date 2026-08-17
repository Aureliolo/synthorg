# module-kind: code
"""Shared guarded-GET primitives for the web tools.

``http_request`` and the ``web_fetch`` local rung both need the same two
things: a URL rewritten to the IP the SSRF check actually validated, and a
body read under a hard byte ceiling. They live here rather than on either
caller because a second copy of DNS-rebinding pinning is a copy that can be
fixed in one place and left wrong in the other.
"""

from ipaddress import IPv6Address, ip_address
from urllib.parse import urlparse, urlunparse

import httpx

from synthorg.core.normalization import compare_ci
from synthorg.tools.network_validator import DnsValidationOk


def pin_url(
    url: str,
    headers: dict[str, str],
    validation: DnsValidationOk,
) -> tuple[str, dict[str, str]]:
    """Rewrite *url* to connect to the validated IP (plain HTTP only).

    For plain HTTP the hostname is replaced with the first validated IP and
    ``Host`` is set, closing the DNS-rebinding TOCTOU gap. For HTTPS the
    original URL is returned, because TLS SNI needs the hostname to validate
    the certificate.

    Returns:
        The request URL and a copied header mapping with ``Host`` normalised;
        the caller's mapping is never mutated.
    """
    parsed = urlparse(url)
    normalized_headers = {k: v for k, v in headers.items() if not compare_ci(k, "host")}
    normalized_headers["Host"] = parsed.hostname or ""

    if not validation.resolved_ips or validation.is_https:
        return url, normalized_headers

    pinned_ip = validation.resolved_ips[0]
    port_suffix = f":{parsed.port}" if parsed.port else ""
    try:
        addr = ip_address(pinned_ip)
    except ValueError:
        return url, normalized_headers
    if isinstance(addr, IPv6Address):
        pinned_netloc = f"[{pinned_ip}]{port_suffix}"
    else:
        pinned_netloc = f"{pinned_ip}{port_suffix}"
    return urlunparse(parsed._replace(netloc=pinned_netloc)), normalized_headers


async def stream_bounded(
    url: str,
    method: str,
    *,
    headers: dict[str, str],
    body: str | None,
    timeout: float,  # noqa: ASYNC109 -- passed to httpx, not asyncio
    max_bytes: int,
) -> tuple[bytes, int, httpx.Headers]:
    """Stream a response, reading at most ``max_bytes + 1``.

    The extra byte is what lets the caller distinguish a body that exactly
    fills the ceiling from one that overran it, without buffering the rest.

    Returns:
        The body bytes (capped), the status code, and the response headers.
    """
    budget = max_bytes + 1
    async with (
        httpx.AsyncClient() as client,
        client.stream(
            method=method,
            url=url,
            headers=headers,
            content=body,
            timeout=timeout,
            follow_redirects=False,
        ) as response,
    ):
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            total += len(chunk)
            if total >= budget:
                break
        status_code = response.status_code
        resp_headers = response.headers
    return b"".join(chunks)[:budget], status_code, resp_headers


__all__ = ["pin_url", "stream_bounded"]
