# module-kind: code
"""Shared guarded-GET primitives for the web tools.

``http_request`` and the ``web_fetch`` local rung both need the same two
things: a URL rewritten to the IP the SSRF check actually validated, and a
body read under a hard byte ceiling. They live here rather than on either
caller because a second copy of DNS-rebinding pinning is a copy that can be
fixed in one place and left wrong in the other.
"""

from http import HTTPStatus
from ipaddress import IPv6Address, ip_address
from urllib.parse import ParseResult, urlparse, urlunparse

import httpx

from synthorg.core.normalization import compare_ci
from synthorg.tools.network_validator import DnsValidationOk


def _explicit_port(parsed: ParseResult) -> int | None:
    """The URL's explicit port, or ``None``.

    Returns:
        The port, or ``None`` when the URL states none or states one that is
        not a number. A malformed port is the target's problem to reject, not
        a reason for the guarded fetch to raise before it is ever sent.
    """
    try:
        return parsed.port
    except ValueError:
        return None


def _host_header(parsed: ParseResult) -> str:
    """Build the RFC 7230 ``Host`` value for *parsed*.

    The authority minus any userinfo, which means the port travels with the
    hostname whenever the URL states one: a target virtual-hosting on a
    non-default port routes on this header, and dropping the port sends it to
    whatever answers on the bare name instead.

    Returns:
        ``host``, ``host:port``, or ``[v6]:port`` for an IPv6 literal, which
        needs the brackets to keep its own colons apart from the port's.
    """
    hostname = parsed.hostname or ""
    if not hostname:
        return ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = _explicit_port(parsed)
    return f"{host}:{port}" if port else host


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
    normalized_headers["Host"] = _host_header(parsed)

    if not validation.resolved_ips or validation.is_https:
        return url, normalized_headers

    pinned_ip = validation.resolved_ips[0]
    port = _explicit_port(parsed)
    port_suffix = f":{port}" if port else ""
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
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, int, httpx.Headers]:
    """Stream a response, reading at most ``max_bytes + 1``.

    The extra byte is what lets the caller distinguish a body that exactly
    fills the ceiling from one that overran it, without buffering the rest.

    Args:
        url: Absolute request URL.
        method: HTTP method.
        headers: Request headers, sent as given.
        body: Request body, or ``None``.
        timeout: Per-request timeout in seconds.
        max_bytes: Hard ceiling on the bytes read from the response.
        transport: Transport to send on, for a caller that pins DNS itself
            rather than by URL rewriting (HTTPS, where TLS needs the name).

    Returns:
        The body bytes (capped), the status code, and the response headers.
    """
    budget = max_bytes + 1
    async with (
        httpx.AsyncClient(transport=transport) as client,
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


def decode_body(raw: bytes, headers: httpx.Headers) -> str:
    """Decode a response body using the charset the response declared.

    Assuming UTF-8 is wrong for a large part of the web: a page served as
    ``windows-1252`` or ``shift_jis`` decodes into replacement characters,
    and the extractor downstream then reports a page with no readable
    content rather than a page it read with the wrong alphabet.

    Returns:
        The decoded text. Undecodable bytes become replacement characters
        rather than raising: a page that is 99% readable is worth more to the
        agent than an exception, and the alternative is discarding content the
        origin actually served.
    """
    declared = httpx.Response(HTTPStatus.OK, headers=headers).charset_encoding
    try:
        return raw.decode(declared or "utf-8", errors="replace")
    except LookupError:
        # The origin named an encoding this runtime does not have. Its label
        # is unusable, but its bytes are not.
        return raw.decode("utf-8", errors="replace")


__all__ = ["decode_body", "pin_url", "stream_bounded"]
