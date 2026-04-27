"""Unit tests for ETag / If-None-Match / 304 middleware (#1600 Phase 4)."""

# Loosely-typed ASGI stubs; runtime behaviour is correct.
# mypy: disable-error-code=arg-type

import asyncio
from typing import Any

import pytest

from synthorg.api.etag import ETagMiddleware, compute_etag, match_etag


@pytest.mark.unit
class TestComputeEtag:
    """``compute_etag`` produces stable weak ETags."""

    def test_stable_for_same_payload(self) -> None:
        assert compute_etag(b"hello") == compute_etag(b"hello")

    def test_changes_when_payload_changes(self) -> None:
        assert compute_etag(b"a") != compute_etag(b"b")

    def test_format_is_weak_etag(self) -> None:
        etag = compute_etag(b"x")
        assert etag.startswith('W/"')
        assert etag.endswith('"')


@pytest.mark.unit
class TestMatchEtag:
    """``match_etag`` handles the three RFC 9110 If-None-Match shapes."""

    def test_none_does_not_match(self) -> None:
        assert not match_etag(None, 'W/"abc"')

    def test_star_matches_anything(self) -> None:
        assert match_etag("*", 'W/"deadbeef"')

    def test_exact_match_with_weak_prefix(self) -> None:
        assert match_etag('W/"abc"', 'W/"abc"')

    def test_match_strips_weak_prefix_on_either_side(self) -> None:
        # If-None-Match always uses weak comparison; either side
        # carrying the W/ prefix should still match.
        assert match_etag('"abc"', 'W/"abc"')
        assert match_etag('W/"abc"', '"abc"')

    def test_no_match_returns_false(self) -> None:
        assert not match_etag('W/"abc"', 'W/"xyz"')

    def test_handles_comma_separated_list(self) -> None:
        assert match_etag('W/"a", W/"b", W/"c"', 'W/"b"')


# -- Middleware integration tests --------------------------------------------


def _ok_app_factory(body: bytes) -> Any:
    """Return a stub ASGI app that responds with ``body`` and HTTP 200."""

    async def app(
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope["type"] != "http":
            return
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            },
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
                "more_body": False,
            },
        )

    return app


class _Recorder:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


async def _empty_receive() -> dict[str, Any]:  # pragma: no cover
    return {"type": "http.disconnect"}


def _http_scope(
    *,
    path: str,
    method: str = "GET",
    if_none_match: str | None = None,
) -> dict[str, Any]:
    headers: list[tuple[bytes, bytes]] = []
    if if_none_match is not None:
        headers.append((b"if-none-match", if_none_match.encode("latin-1")))
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "server": ("test", 80),
        "client": ("test-client", 1234),
    }


@pytest.mark.unit
class TestETagMiddleware:
    """End-to-end ASGI middleware behaviour."""

    async def test_get_in_scope_adds_etag_header(self) -> None:
        body = b'{"value":1}'
        mw = ETagMiddleware(_ok_app_factory(body))
        recorder = _Recorder()
        await mw(_http_scope(path="/api/v1/settings"), _empty_receive, recorder)
        assert recorder.messages[0]["status"] == 200
        headers = dict(recorder.messages[0]["headers"])
        assert b"etag" in headers
        assert headers[b"etag"].startswith(b'W/"')
        assert recorder.messages[1]["body"] == body

    async def test_get_out_of_scope_passes_through(self) -> None:
        body = b'{"value":1}'
        mw = ETagMiddleware(_ok_app_factory(body))
        recorder = _Recorder()
        await mw(_http_scope(path="/api/v1/tasks"), _empty_receive, recorder)
        headers = dict(recorder.messages[0]["headers"])
        assert b"etag" not in headers
        assert recorder.messages[1]["body"] == body

    async def test_post_in_scope_passes_through(self) -> None:
        """Only GETs get ETag treatment; POSTs are passed through."""
        body = b'{"created":true}'
        mw = ETagMiddleware(_ok_app_factory(body))
        recorder = _Recorder()
        await mw(
            _http_scope(path="/api/v1/settings", method="POST"),
            _empty_receive,
            recorder,
        )
        headers = dict(recorder.messages[0]["headers"])
        assert b"etag" not in headers

    async def test_matching_if_none_match_returns_304(self) -> None:
        body = b'{"value":1}'
        # Pre-compute the ETag the server will produce, then send it.
        etag = compute_etag(body)
        mw = ETagMiddleware(_ok_app_factory(body))
        recorder = _Recorder()
        await mw(
            _http_scope(path="/api/v1/settings", if_none_match=etag),
            _empty_receive,
            recorder,
        )
        assert recorder.messages[0]["status"] == 304
        assert recorder.messages[1]["body"] == b""

    async def test_non_matching_if_none_match_returns_200(self) -> None:
        mw = ETagMiddleware(_ok_app_factory(b'{"value":1}'))
        recorder = _Recorder()
        await mw(
            _http_scope(path="/api/v1/settings", if_none_match='W/"stale"'),
            _empty_receive,
            recorder,
        )
        assert recorder.messages[0]["status"] == 200
        headers = dict(recorder.messages[0]["headers"])
        assert b"etag" in headers

    async def test_etag_changes_when_body_changes(self) -> None:
        recorder1 = _Recorder()
        await ETagMiddleware(_ok_app_factory(b'{"value":1}'))(
            _http_scope(path="/api/v1/settings"),
            _empty_receive,
            recorder1,
        )
        etag1 = dict(recorder1.messages[0]["headers"]).get(b"etag")

        recorder2 = _Recorder()
        await ETagMiddleware(_ok_app_factory(b'{"value":2}'))(
            _http_scope(path="/api/v1/settings"),
            _empty_receive,
            recorder2,
        )
        etag2 = dict(recorder2.messages[0]["headers"]).get(b"etag")

        assert etag1 is not None
        assert etag2 is not None
        assert etag1 != etag2

    async def test_cache_control_default_private_for_user_data(self) -> None:
        mw = ETagMiddleware(_ok_app_factory(b"{}"))
        recorder = _Recorder()
        await mw(
            _http_scope(path="/api/v1/settings"),
            _empty_receive,
            recorder,
        )
        headers = dict(recorder.messages[0]["headers"])
        assert b"cache-control" in headers
        assert b"private" in headers[b"cache-control"]

    async def test_cache_control_default_public_for_reference_data(self) -> None:
        mw = ETagMiddleware(_ok_app_factory(b"[]"))
        recorder = _Recorder()
        await mw(
            _http_scope(path="/api/v1/providers"),
            _empty_receive,
            recorder,
        )
        headers = dict(recorder.messages[0]["headers"])
        assert b"cache-control" in headers
        assert b"public" in headers[b"cache-control"]

    async def test_non_200_response_passes_through(self) -> None:
        """4xx/5xx responses are not ETag'd."""

        async def err_app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [(b"content-type", b"application/json")],
                },
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"error":"not found"}',
                    "more_body": False,
                },
            )

        mw = ETagMiddleware(err_app)
        recorder = _Recorder()
        await mw(
            _http_scope(path="/api/v1/settings"),
            _empty_receive,
            recorder,
        )
        assert recorder.messages[0]["status"] == 404
        headers = dict(recorder.messages[0]["headers"])
        assert b"etag" not in headers

    async def test_lifespan_scope_passes_through(self) -> None:
        captured: list[dict[str, Any]] = []

        async def app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            captured.append(scope)

        await ETagMiddleware(app)(
            {"type": "lifespan"},
            _empty_receive,
            _Recorder(),
        )
        assert captured == [{"type": "lifespan"}]

    async def test_concurrent_requests_do_not_share_state(self) -> None:
        """Two concurrent requests to the same middleware get independent ETags."""
        bodies = [b'{"a":1}', b'{"b":2}']

        async def app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            body = bodies[scope["client"][1] % len(bodies)]
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                },
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                },
            )

        mw = ETagMiddleware(app)
        recorders = [_Recorder() for _ in range(2)]
        scopes = [_http_scope(path="/api/v1/settings") for _ in range(2)]
        # Different client ports route to different bodies in the stub.
        scopes[0]["client"] = ("a", 0)
        scopes[1]["client"] = ("b", 1)
        await asyncio.gather(
            mw(scopes[0], _empty_receive, recorders[0]),
            mw(scopes[1], _empty_receive, recorders[1]),
        )
        etag0 = dict(recorders[0].messages[0]["headers"]).get(b"etag")
        etag1 = dict(recorders[1].messages[0]["headers"]).get(b"etag")
        assert etag0 != etag1

    async def test_304_strips_content_length_and_content_type(self) -> None:
        """304 Not Modified must drop body-shape headers per RFC 7232."""
        body = b'{"value":1}'
        etag = compute_etag(body)

        async def app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                },
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                },
            )

        recorder = _Recorder()
        await ETagMiddleware(app)(
            _http_scope(path="/api/v1/settings", if_none_match=etag),
            _empty_receive,
            recorder,
        )
        headers = dict(recorder.messages[0]["headers"])
        assert recorder.messages[0]["status"] == 304
        assert b"etag" in headers
        assert b"cache-control" in headers
        assert b"content-length" not in headers
        assert b"content-type" not in headers

    async def test_existing_cache_control_is_not_overwritten(self) -> None:
        """The middleware must respect an upstream-set Cache-Control header."""

        async def app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"cache-control", b"no-store"),
                    ],
                },
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"{}",
                    "more_body": False,
                },
            )

        recorder = _Recorder()
        await ETagMiddleware(app)(
            _http_scope(path="/api/v1/settings"),
            _empty_receive,
            recorder,
        )
        cache_values = [
            v for k, v in recorder.messages[0]["headers"] if k == b"cache-control"
        ]
        assert cache_values == [b"no-store"]

    async def test_streaming_response_skips_etag_and_buffers_nothing(self) -> None:
        """Multi-chunk responses are forwarded as-is with no ETag and no buffering."""
        chunks = [b"chunk-1", b"chunk-2", b"chunk-3"]

        async def streaming_app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                },
            )
            for idx, chunk in enumerate(chunks):
                await send(
                    {
                        "type": "http.response.body",
                        "body": chunk,
                        "more_body": idx < len(chunks) - 1,
                    },
                )

        recorder = _Recorder()
        await ETagMiddleware(streaming_app)(
            _http_scope(path="/api/v1/settings"),
            _empty_receive,
            recorder,
        )
        # 1 start + 3 body messages, all forwarded as-is.
        assert len(recorder.messages) == 1 + len(chunks)
        headers = dict(recorder.messages[0]["headers"])
        assert b"etag" not in headers
        assert b"cache-control" not in headers
        bodies = [m["body"] for m in recorder.messages[1:]]
        assert bodies == chunks
        # The middle chunks must keep ``more_body=True``; only the last is False.
        assert recorder.messages[-1]["more_body"] is False
        for msg in recorder.messages[1:-1]:
            assert msg["more_body"] is True

    async def test_inner_app_returns_without_final_chunk_flushes_buffer(
        self,
    ) -> None:
        """Inner app returns mid-buffer; middleware flushes start + buffered body."""

        async def truncating_app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                },
            )
            # Returns without sending an ``http.response.body``;
            # the middleware must still close out the response.

        recorder = _Recorder()
        await ETagMiddleware(truncating_app)(
            _http_scope(path="/api/v1/settings"),
            _empty_receive,
            recorder,
        )
        assert len(recorder.messages) == 2
        assert recorder.messages[0]["status"] == 200
        assert recorder.messages[1]["body"] == b""
        assert recorder.messages[1]["more_body"] is False
