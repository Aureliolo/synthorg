"""Conditional GET (ETag / If-None-Match / 304) ASGI middleware.

Implemented as a thin ASGI middleware so every read-only GET in
the configured path-prefix allowlist gains weak-ETag + ``304 Not
Modified`` support without per-handler changes. Wraps the outbound
``send`` to:

1. Capture the ``http.response.start`` (status + headers) and
   subsequent ``http.response.body`` chunks. Streaming responses
   (the first ``http.response.body`` arrives with ``more_body=True``)
   short-circuit to a pure pass-through: no buffering, no ETag.
2. Compute a weak ETag (``W/"<sha256-prefix>"``) over the assembled
   body using ``hashlib.sha256`` so the value is byte-stable across
   Python versions and processes (no ``msgspec`` round-trip needed).
3. If the client sent ``If-None-Match`` and the ETag matches,
   replace the response with ``304 Not Modified`` (empty body,
   keeping the validator-friendly ``ETag`` and ``Cache-Control``
   headers).
4. Otherwise forward the original response with the ``ETag`` header
   appended.

Only applies to GET requests on path prefixes in the allowlist:
``/api/v1/settings``, ``/api/v1/agents``, ``/api/v1/template-packs``,
``/api/v1/providers``, ``/api/v1/ontology``, ``/api/v1/departments``,
``/api/v1/company``, ``/api/v1/meta/analytics``,
``/api/v1/healthz``, ``/api/v1/readyz``. Prefix matching requires
either an exact match or a ``/`` boundary so siblings like
``/api/v1/providers-extra`` do not get accidental cache treatment.

The ``Cache-Control`` companion header is **always replaced**
(not just appended-when-missing) on allowlisted responses:
``private, must-revalidate`` for user-scoped data and
``public, max-age=0, must-revalidate`` for deployment-wide
reference data (``template-packs``, ``providers``, ``ontology``,
``healthz``, ``readyz``). The replace is required because the
global ``security_headers_hook`` runs as a Litestar
``before_send`` and unconditionally pins
``Cache-Control: no-store, no-cache, must-revalidate, max-age=0``
on every API response before this middleware sees it; without an
explicit overwrite, allowlisted reads would never advertise the
validator-friendly policy and clients would not retain ETags.
"""

import asyncio
import hashlib
from dataclasses import dataclass, field

from litestar.types import ASGIApp, Receive, Scope, Send

from synthorg.observability import get_logger
from synthorg.observability.events.api import API_ETAG_CACHE_HIT

logger = get_logger(__name__)


@dataclass(slots=True)
class _CaptureState:
    """Mutable state shared between ETagMiddleware and its inner _capturing_send."""

    captured_start: dict[str, object] | None = None
    captured_body: bytearray = field(default_factory=bytearray)
    body_complete: bool = False
    passthrough: bool = False


# Path prefixes that get conditional-GET treatment. Each entry is a
# string the middleware matches with ``str.startswith`` against the
# (decoded) request path. Adding a new endpoint group is a matter of
# appending here.
_ETAG_PATH_PREFIXES: tuple[str, ...] = (
    "/api/v1/settings",
    "/api/v1/agents",
    "/api/v1/template-packs",
    "/api/v1/providers",
    "/api/v1/ontology",
    "/api/v1/departments",
    "/api/v1/company",
    "/api/v1/meta/analytics",
    "/api/v1/security/audit",
    "/api/v1/tasks",
    "/api/v1/activities",
    "/api/v1/healthz",
    "/api/v1/readyz",
)

# Path prefixes that are deployment-wide reference data (public
# cache control); everything else gets ``private, must-revalidate``.
_PUBLIC_CACHE_PREFIXES: tuple[str, ...] = (
    "/api/v1/template-packs",
    "/api/v1/providers",
    "/api/v1/ontology",
    "/api/v1/healthz",
    "/api/v1/readyz",
)

_DEFAULT_PRIVATE_CACHE: bytes = b"private, must-revalidate"
_DEFAULT_PUBLIC_CACHE: bytes = b"public, max-age=0, must-revalidate"

_HTTP_OK: int = 200
_HTTP_NOT_MODIFIED: int = 304


def compute_etag(body: bytes) -> str:
    """Return a weak ETag over ``body``.

    Format: ``W/"<32-hex-chars>"`` -- 16 bytes of sha256 prefix
    encoded as 32 hex chars, wrapped in the weak-ETag prefix per
    RFC 9110. Weak comparison is appropriate for ``If-None-Match``
    (a weak ETag still proves semantic equivalence even if the
    serialised bytes differ).

    Returns:
        Resulting string.
    """
    digest = hashlib.sha256(body).hexdigest()[:32]
    return f'W/"{digest}"'


def _split_entity_tags(value: str) -> list[str]:
    """Split an ``If-None-Match`` header value into RFC 9110 §13.1 tags.

    A bare ``str.split(",")`` would mis-handle a quoted entity-tag
    that contains a literal comma inside the quoted-string body
    (the spec allows it via the ``quoted-string`` production); the
    legitimate validator carrying the matching tag would then fail
    the 304 path. This walker tracks whether we are inside a
    quoted-string and only treats commas at the top level as
    separators, matching ``1#entity-tag = entity-tag *( OWS "," OWS entity-tag )``.

    Returns:
        List of the declared element type.
    """
    tags: list[str] = []
    buf: list[str] = []
    in_quote = False
    escaped = False
    for ch in value:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if in_quote:
            if ch == "\\":
                escaped = True
            elif ch == '"':
                in_quote = False
            buf.append(ch)
            continue
        if ch == '"':
            in_quote = True
            buf.append(ch)
            continue
        if ch == ",":
            token = "".join(buf).strip()
            if token:
                tags.append(token)
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        tags.append(tail)
    return tags


def match_etag(if_none_match: str | None, etag: str) -> bool:
    """Return ``True`` when ``if_none_match`` matches ``etag``.

    Handles ``*`` (matches any current representation), comma-
    separated entity-tag lists, and weak-vs-strong comparison. Per
    RFC 9110 §13.1.2, ``If-None-Match`` always uses weak comparison,
    so we strip the leading ``W/`` from both candidates before
    comparing.

    Comma splitting goes through :func:`_split_entity_tags` so a
    quoted-string body that contains a literal comma is preserved
    inside its tag rather than splitting it across two pseudo-tags.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    if if_none_match is None:
        return False
    candidate = if_none_match.strip()
    if candidate == "*":
        return True
    target = etag.removeprefix("W/")
    for raw in _split_entity_tags(candidate):
        normalised = raw.removeprefix("W/")
        if normalised == target:
            return True
    return False


def _matches_path_prefix(path: str, prefix: str) -> bool:
    """Return ``True`` when ``path`` is exactly ``prefix`` or a sub-path.

    Bare ``str.startswith`` would also accept siblings like
    ``/api/v1/providers-extra`` for the ``/api/v1/providers`` allowlist
    entry, leaking ETag/cache treatment to unrelated routes. Require
    either an exact match or a ``/`` boundary so only the intended
    routes qualify.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    return path == prefix or path.startswith(f"{prefix}/")


def _is_etag_path(path: str) -> bool:
    """Return ``True`` when ``path`` is in the allowlist.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    return any(_matches_path_prefix(path, prefix) for prefix in _ETAG_PATH_PREFIXES)


def _is_public_cache_path(path: str) -> bool:
    """Return ``True`` when ``path`` should advertise ``public`` cache.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    return any(_matches_path_prefix(path, prefix) for prefix in _PUBLIC_CACHE_PREFIXES)


def _read_if_none_match(headers: list[tuple[bytes, bytes]]) -> str | None:
    """Extract the ``If-None-Match`` header value (case-insensitive).

    HTTP allows the same header to appear more than once; per RFC 9110
    repeated values are equivalent to a single value with the entries
    joined by ``,``. Returning the first occurrence only would mean a
    matching tag carried by a later occurrence loses the 304 path.
    Build a comma-joined merged value so :func:`match_etag` walks all
    of them.

    Returns:
        The ``str`` value when present, ``None`` otherwise.
    """
    # Bytes-typed ASGI header lowering is exempt from
    # ``compare_ci`` (str-only); see normalization.py.
    values = [
        value.decode("latin-1")
        for name, value in headers
        if name.lower() == b"if-none-match"
    ]
    return ", ".join(values) if values else None


class ETagMiddleware:
    """ASGI middleware that adds conditional-GET to allowlisted GETs.

    Args:
        app: The wrapped ASGI app (the Litestar instance).
    """

    __slots__ = ("_app",)

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Wrap the response if the request is in scope for ETag handling."""
        scope_type = scope["type"]
        if scope_type != "http":  # type: ignore[comparison-overlap]
            await self._app(scope, receive, send)
            return
        method = scope.get("method", "")
        path = scope.get("path", "")
        if method != "GET" or not _is_etag_path(path):
            await self._app(scope, receive, send)
            return

        request_headers: list[tuple[bytes, bytes]] = list(
            scope.get("headers", []),
        )
        if_none_match = _read_if_none_match(request_headers)
        state = _CaptureState()

        async def _capturing_send(message: dict[str, object]) -> None:
            """Run capturing send."""
            msg_type = message.get("type")
            if msg_type == "http.response.start":
                state.captured_start = dict(message)
                return
            if msg_type == "http.response.body":
                await _handle_body_message(
                    message,
                    state,
                    send,
                    if_none_match=if_none_match,
                    path=path,
                )
                return
            # Pass-through for any unexpected message types.
            await send(message)  # type: ignore[arg-type]

        await self._app(scope, receive, _capturing_send)  # type: ignore[arg-type]
        if state.passthrough or state.body_complete:
            return
        if state.captured_start is None:
            return
        # Inner app returned without completing the buffered response;
        # flush the captured start + buffered body so the connection
        # terminates cleanly.
        await _emit_passthrough(
            send,
            state.captured_start,
            bytes(state.captured_body),
        )


def _apply_cache_control(
    headers: list[tuple[bytes, bytes]],
    path: str,
) -> list[tuple[bytes, bytes]]:
    """Replace ``cache-control`` with the validator-friendly policy.

    Drops any existing ``cache-control`` and appends
    ``_DEFAULT_PUBLIC_CACHE`` / ``_DEFAULT_PRIVATE_CACHE`` by path. The
    replace (not append-if-missing) is required because the global
    ``security_headers_hook`` runs as a Litestar ``before_send`` and
    unconditionally pins ``Cache-Control: no-store, no-cache,
    must-revalidate, max-age=0`` on every API response before this
    middleware sees it; without the overwrite, allowlisted reads
    (buffered AND streaming) would never advertise the
    ``private``/``public`` policy and clients would not revalidate.
    Shared by :func:`_emit_response` (buffered, also adds an ETag) and
    the streaming pass-through branch (no ETag, cache policy only).

    Returns:
        List of the declared element type.
    """
    cache_default = (
        _DEFAULT_PUBLIC_CACHE if _is_public_cache_path(path) else _DEFAULT_PRIVATE_CACHE
    )
    rewritten = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
    rewritten.append((b"cache-control", cache_default))
    return rewritten


async def _emit_response(
    send: Send,
    captured_start: dict[str, object] | None,
    body: bytes,
    if_none_match: str | None,
    *,
    path: str,
) -> None:
    """Replay the captured response with ETag + optional 304 short-circuit."""
    if captured_start is None:
        return
    status_value = captured_start.get("status", _HTTP_OK)
    status = status_value if isinstance(status_value, int) else _HTTP_OK
    if status != _HTTP_OK:
        await _emit_passthrough(send, captured_start, body)
        return

    headers_value = captured_start.get("headers", [])
    headers: list[tuple[bytes, bytes]] = (
        list(headers_value) if isinstance(headers_value, list | tuple) else []
    )
    # Offload the SHA-256 digest off the event loop; a large buffered
    # body would otherwise block other requests during hashing.
    etag = await asyncio.to_thread(compute_etag, body)
    # Cache-Control policy is shared with the streaming branch via
    # ``_apply_cache_control``; the ETag is buffered-only so it is
    # dropped + reinstalled here, not in the shared helper.
    without_etag = [(k, v) for k, v in headers if k.lower() != b"etag"]
    extended_headers = _apply_cache_control(without_etag, path)
    extended_headers.append((b"etag", etag.encode("latin-1")))

    if match_etag(if_none_match, etag):
        # DEBUG-only: every 304 saves a body roundtrip; logging at
        # debug keeps the hot path quiet but surfaces cache-hit
        # rates when an operator pulls debug-level logs to verify
        # client-side validator handling.
        logger.debug(API_ETAG_CACHE_HIT, path=path, etag=etag)
        await _emit_not_modified(send, extended_headers)
        return
    await _emit_with_etag(send, captured_start, body, extended_headers)


async def _emit_passthrough(
    send: Send,
    captured_start: dict[str, object],
    body: bytes,
) -> None:
    """Forward a non-200 (or final-flush) response with no ETag handling.

    The captured ``Content-Length`` (if any) is replaced with
    ``len(body)`` because the truncation-fallback path may have
    captured fewer bytes than the inner app declared; an unmatched
    length would produce an invalid response on the very cleanup path
    this helper exists to make safe.
    """
    headers_value = captured_start.get("headers", [])
    headers: list[tuple[bytes, bytes]] = (
        list(headers_value) if isinstance(headers_value, list | tuple) else []
    )
    # Bytes-typed ASGI header lowering is exempt from
    # ``compare_ci`` (str-only); see normalization.py.
    headers = [(k, v) for k, v in headers if k.lower() != b"content-length"]
    headers.append((b"content-length", str(len(body)).encode("ascii")))
    forwarded_start = dict(captured_start)
    forwarded_start["headers"] = headers
    await send(forwarded_start)  # type: ignore[arg-type]
    await send(
        {
            "type": "http.response.body",
            "body": body,
            "more_body": False,
        },
    )


async def _emit_not_modified(
    send: Send,
    extended_headers: list[tuple[bytes, bytes]],
) -> None:
    """Send a 304 Not Modified, stripping body-shape headers per RFC 7232."""
    not_modified_headers = [
        (k, v)
        for k, v in extended_headers
        if k.lower() not in {b"content-length", b"content-type"}
    ]
    await send(
        {
            "type": "http.response.start",
            "status": _HTTP_NOT_MODIFIED,
            "headers": not_modified_headers,
        },
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"",
            "more_body": False,
        },
    )


async def _emit_with_etag(
    send: Send,
    captured_start: dict[str, object],
    body: bytes,
    extended_headers: list[tuple[bytes, bytes]],
) -> None:
    """Forward the 200 OK response with the new ETag/Cache-Control headers."""
    forwarded_start = dict(captured_start)
    forwarded_start["headers"] = extended_headers
    await send(forwarded_start)  # type: ignore[arg-type]
    await send(
        {
            "type": "http.response.body",
            "body": body,
            "more_body": False,
        },
    )


async def _handle_body_message(
    message: dict[str, object],
    state: _CaptureState,
    send: Send,
    *,
    if_none_match: str | None,
    path: str,
) -> None:
    """Process an ``http.response.body`` message captured by the middleware.

    Branches on whether we are already in pass-through mode, whether
    this is the first chunk of a streaming (multi-chunk) response, or
    whether this is the final body of a buffered single-chunk response
    that gets full ETag treatment.
    """
    if state.passthrough:
        await send(message)  # type: ignore[arg-type]
        return
    if message.get("more_body", False):
        # Multi-chunk response: stream as-is, no ETag, no buffering.
        # The body cannot be hashed without buffering, so no ETag is
        # emitted -- but the validator-friendly Cache-Control policy
        # still applies (otherwise the global ``no-store`` from
        # ``security_headers_hook`` would suppress client revalidation
        # for streamed allowlisted reads too).
        if state.captured_start is not None:
            headers_value = state.captured_start.get("headers", [])
            current_headers: list[tuple[bytes, bytes]] = (
                list(headers_value) if isinstance(headers_value, list | tuple) else []
            )
            forwarded_start = dict(state.captured_start)
            forwarded_start["headers"] = _apply_cache_control(
                current_headers,
                path,
            )
            await send(forwarded_start)  # type: ignore[arg-type]
            state.captured_start = None
        await send(message)  # type: ignore[arg-type]
        state.passthrough = True
        return
    body = message.get("body", b"")
    if isinstance(body, bytes | bytearray):
        state.captured_body.extend(body)
    state.body_complete = True
    await _emit_response(
        send,
        state.captured_start,
        bytes(state.captured_body),
        if_none_match,
        path=path,
    )
    state.captured_start = None
