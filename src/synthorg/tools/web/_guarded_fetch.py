# module-kind: code
"""Shared guarded-GET primitives for the web tools.

``http_request`` and the ``web_fetch`` local rung both need the same two
things: a URL rewritten to the IP the SSRF check actually validated, and a
body read under a hard byte ceiling. They live here rather than on either
caller because a second copy of DNS-rebinding pinning is a copy that can be
fixed in one place and left wrong in the other.
"""

import asyncio
from http import HTTPStatus
from ipaddress import IPv6Address, ip_address
from typing import Final
from urllib.parse import ParseResult, SplitResult, urlparse, urlunparse

import httpx

from synthorg.core.normalization import compare_ci
from synthorg.core.resilience.retry_after import (
    coerce_finite_nonneg_seconds,
    parse_retry_after_seconds,
)
from synthorg.tools._dns_pinning import PinnedDnsTransport
from synthorg.tools.errors import ToolParameterError
from synthorg.tools.network_validator import DnsValidationOk

# How many per-operation timeouts a whole exchange may take before it is
# abandoned. Generous, because a large page over a slow link legitimately
# spans several reads and this is a backstop against an unbounded hold, not a
# performance budget.
_TOTAL_DEADLINE_MULTIPLIER: Final[int] = 6

#: Statuses that mean "ask again", shared by every rung so a page that would
#: have answered on retry is not classified as unreadable by one backend and
#: retryable by another. Deliberately not "any 5xx": 501, 505 and 507 name a
#: permanent condition, and retrying them only spends the ladder.
RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})


def _host_and_port(parsed: ParseResult | SplitResult) -> tuple[str, int | None] | None:
    """Split *parsed* into its host and the port it explicitly states.

    One answer for both, because the two are decided together: a URL states a
    port or it does not, and a URL stating something that is not a port has no
    authority anything here can act on. Reporting that last case as "no port
    stated" is the trap. It is the same shape as reading ``:0`` as absent, and
    it fails the same way: the consumers that run BEFORE the network validator
    derive a URL on the scheme default, which names a different endpoint from
    the one the caller gave and, unlike that one, resolves.

    Returns:
        The host and the stated port, or ``None`` when the URL states no host
        or states a port that is not one (out of range, or not a number). The
        host is bracketed for an IPv6 literal so its own colons stay apart
        from the port separator.
    """
    hostname = parsed.hostname or ""
    if not hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return (f"[{hostname}]" if ":" in hostname else hostname), port


def _render_authority(host: str, port: int | None) -> str:
    """Join *host* and *port* into an authority.

    ``is not None``, not truthiness: port 0 is stated, and dropping it would
    rewrite a URL the network validator refuses (it rejects any port at or
    below zero) into one it accepts at the scheme default.

    Returns:
        ``host`` when no port is stated, ``host:port`` otherwise.
    """
    return f"{host}:{port}" if port is not None else host


def authority_of(parsed: ParseResult | SplitResult) -> str:
    """Build the credential-free authority for *parsed*.

    Two things this is not: it is not ``netloc``, which carries any userinfo
    the URL states, and it is not the bare hostname. Userinfo is dropped
    because every consumer here either sends this value somewhere (an RFC 7230
    ``Host``) or shows it to somebody (a derived URL in a tool result, a cache
    key), and a password belongs in none of those. The port is kept because a
    target virtual-hosting on a non-default port routes on it, and dropping it
    sends the request to whatever answers on the bare name instead.

    Returns:
        ``host``, ``host:port``, or ``[v6]:port`` for an IPv6 literal, which
        needs the brackets to keep its own colons apart from the port's. Empty
        when there is no authority to build: the URL states no host, or states
        a port that is not one. Both are URLs nothing here can act on, and
        every consumer already fails closed on the empty answer.
    """
    resolved = _host_and_port(parsed)
    if resolved is None:
        return ""
    return _render_authority(*resolved)


def pin_url(
    url: str,
    headers: dict[str, str],
    validation: DnsValidationOk,
) -> tuple[str, dict[str, str]]:
    """Rewrite *url* to carry the validated hostname, and the validated IP.

    For plain HTTP the hostname is replaced with the first validated IP and
    ``Host`` is set, closing the DNS-rebinding TOCTOU gap. For HTTPS the
    hostname stays, because TLS SNI needs it to validate the certificate.

    Both spellings come from ``validation.hostname``, never from the URL's
    own. httpx re-encodes whatever host the URL carries using its own IDNA
    settings, which are not the ones the guard canonicalised with, so a URL
    still spelling its host as a U-label would be dialled and pinned against
    a second, independently derived name. An A-label takes httpx's
    pure-ASCII path, where no re-encoding happens at all, which is what
    makes the name checked here and the name connected to the same string
    rather than two that usually agree. Userinfo is carried across
    unchanged, since it is part of the request the caller authored.

    Returns:
        The request URL and a copied header mapping with ``Host`` normalised;
        the caller's mapping is never mutated.

    Raises:
        ToolParameterError: If *url* has no authority to pin to. Unreachable
            through a validated call, since ``validate_url_host`` refuses both
            a hostless URL and a malformed port before any caller arrives here,
            so this is a broken invariant rather than an input case. It raises
            because both ways of carrying on lie about where the request goes:
            an empty ``Host`` names no site, and a dropped port names a
            different one. The URL stays out of the message for the reason the
            validator gives at its own hostless branch: redaction rebuilds
            around a parsed hostname and returns its input untouched when there
            is none, so echoing it back would copy out whatever sat in the
            authority.
    """
    parsed = urlparse(url)
    resolved = _host_and_port(parsed)
    if resolved is None:
        msg = "cannot pin a URL with no usable authority"
        raise ToolParameterError(msg)
    _, port = resolved
    validated_host = validation.hostname
    bracketed_host = f"[{validated_host}]" if ":" in validated_host else validated_host
    normalized_headers = {k: v for k, v in headers.items() if not compare_ci(k, "host")}
    normalized_headers["Host"] = _render_authority(bracketed_host, port)
    userinfo = parsed.netloc.rpartition("@")[0]

    def _with_authority(host: str) -> str:
        """Rebuild *url* around *host*, keeping any userinfo it stated.

        Returns:
            The URL with its authority replaced.
        """
        authority = _render_authority(host, port)
        netloc = f"{userinfo}@{authority}" if userinfo else authority
        return urlunparse(parsed._replace(netloc=netloc))

    canonical_url = _with_authority(bracketed_host)
    if not validation.resolved_ips or validation.is_https:
        return canonical_url, normalized_headers

    pinned_ip = validation.resolved_ips[0]
    try:
        addr = ip_address(pinned_ip)
    except ValueError:
        return canonical_url, normalized_headers
    pinned_host = f"[{pinned_ip}]" if isinstance(addr, IPv6Address) else pinned_ip
    return _with_authority(pinned_host), normalized_headers


async def stream_bounded(
    url: str,
    method: str,
    *,
    headers: dict[str, str],
    body: str | None,
    timeout: float,  # noqa: ASYNC109 -- passed to httpx, not asyncio
    max_bytes: int,
    transport: httpx.AsyncBaseTransport | None = None,
    validation: DnsValidationOk | None = None,
) -> tuple[bytes, int, httpx.Headers]:
    """Stream a response, reading at most ``max_bytes + 1``.

    The extra byte is what lets the caller distinguish a body that exactly
    fills the ceiling from one that overran it, without buffering the rest.

    Args:
        url: Absolute request URL.
        method: HTTP method.
        headers: Request headers, sent as given.
        body: Request body, or ``None``.
        timeout: Per-request timeout in seconds. httpx applies this per
            OPERATION, so it bounds one read rather than the whole exchange;
            the wall-clock ceiling below is derived from it.
        max_bytes: Hard ceiling on the bytes read from the response.
        transport: Transport to send on, for a caller that already built its
            own pinned one. Wins over *validation*.
        validation: The SSRF verdict this read is acting on. Supplying it
            closes the HTTPS rebinding window; omitting it means the
            connection re-resolves the name.

    Returns:
        The body bytes (capped), the status code, and the response headers.

    Raises:
        TimeoutError: If the exchange outlives the total deadline.
    """
    budget = max_bytes + 1
    owned = transport is None and validation is not None
    send_on = transport if transport is not None else _pinned_transport(validation)
    try:
        return await _read_bounded(
            url,
            method,
            headers=headers,
            body=body,
            timeout=timeout,
            budget=budget,
            transport=send_on,
        )
    finally:
        # Only what this call built. A transport handed in belongs to the
        # caller, which closes it on its own schedule.
        if owned and send_on is not None:
            await send_on.aclose()


def _pinned_transport(
    validation: DnsValidationOk | None,
) -> PinnedDnsTransport | None:
    """Build a DNS-pinned transport for a validated HTTPS target.

    Plain HTTP needs none: :func:`pin_url` has already rewritten the URL to
    the validated address, so there is no name left to re-resolve. HTTPS keeps
    its hostname because TLS verifies the certificate against it, which is
    what leaves a second lookup between the check and the connection: an
    attacker's DNS can answer public for the validation and private for the
    connect. Pinning the transport closes that without touching SNI, which
    httpcore carries separately from the address it dials.

    Returns:
        The transport, or ``None`` when there is nothing to pin.
    """
    if validation is None or not validation.is_https or not validation.resolved_ips:
        return None
    return PinnedDnsTransport(
        hostname=validation.hostname,
        ip=validation.resolved_ips[0],
    )


async def _read_bounded(
    url: str,
    method: str,
    *,
    headers: dict[str, str],
    body: str | None,
    timeout: float,  # noqa: ASYNC109 -- passed to httpx, not asyncio
    budget: int,
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[bytes, int, httpx.Headers]:
    """Perform the bounded read itself.

    Returns:
        The body bytes (capped), the status code, and the response headers.

    Raises:
        TimeoutError: If the exchange outlives the total deadline.
    """
    # A per-operation timeout bounds each read, not the sequence of them: a
    # server dripping one byte just inside every read window holds the
    # coroutine for one timeout per chunk, which at these ceilings is
    # effectively forever. Nothing above this imposes a wall-clock cap, so it
    # belongs here, on the one seam every guarded read passes through.
    async with asyncio.timeout(timeout * _TOTAL_DEADLINE_MULTIPLIER):
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


def retry_after_seconds(headers: httpx.Headers) -> float | None:
    """Parse a ``Retry-After`` response header into seconds.

    Returns:
        The parsed non-negative delay, or ``None`` when the header is absent
        or states something that is not a delay.
    """
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    return coerce_finite_nonneg_seconds(parse_retry_after_seconds(raw))


def clamped_retry_after(headers: httpx.Headers, *, ceiling: float) -> float | None:
    """Parse ``Retry-After`` and cap it at *ceiling*.

    The cap belongs to the caller because how far a stated cooldown may be
    trusted is a property of who stated it, and every rung reads this header
    from a different party. It is a cap rather than advice because a retry
    handler honours a server-supplied override PAST its own configured cap by
    design, so whatever survives here is the real upper bound on how long one
    tool call can sit still.

    Returns:
        The capped delay, or ``None`` when the header is absent or states
        something that is not a delay. ``min`` is a cap and not a passthrough
        only because :func:`retry_after_seconds` has already excluded NaN and
        both infinities: ``min(nan, x)`` answers ``nan``.
    """
    asked = retry_after_seconds(headers)
    if asked is None:
        return None
    return min(asked, ceiling)


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


__all__ = [
    "RETRYABLE_STATUSES",
    "authority_of",
    "clamped_retry_after",
    "decode_body",
    "pin_url",
    "retry_after_seconds",
    "stream_bounded",
]
