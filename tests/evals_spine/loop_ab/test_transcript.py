# module-kind: tests
"""The transcript tap has to record what the client read, not what the wire carried.

The host compresses any response over ``api.compression_minimum_size_bytes``,
and the tap wraps the whole application, so it observes the compressed bytes.
A completion carrying a written file in its tool-call arguments clears that
threshold easily, which is exactly the exchange a reviewer wants to read.
"""

import gzip
import json
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Final, cast

import brotli
import pytest
from litestar.types import (
    ASGIApp,
    HTTPRequestEvent,
    HTTPScope,
    Message,
    Receive,
    ReceiveMessage,
    Scope,
    Send,
)

from evals.loop_ab.transcript import TranscriptRecorder, transcribing

pytestmark = pytest.mark.unit

_BEARER: Final = "secret-bearer-value"

_RESPONSE_BODY: Final[dict[str, object]] = {
    "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 4096, "completion_tokens": 128},
}


def _scope(path: str) -> Scope:
    """An HTTP scope for *path*, carrying the bearer the recorder mints.

    Returns:
        The scope.
    """
    return cast(
        "HTTPScope",
        {
            "type": "http",
            "path": path,
            "method": "POST",
            "headers": [(b"authorization", f"Bearer {_BEARER}".encode())],
        },
    )


def _app_sending(body: bytes, *, encoding: str | None) -> ASGIApp:
    """Build an ASGI app answering with *body* under *encoding*.

    Returns:
        The application.
    """
    headers = [(b"content-type", b"application/json")]
    if encoding is not None:
        headers.append((b"content-encoding", encoding.encode()))

    async def _app(scope: Scope, receive: Receive, send: Send) -> None:
        while (await receive()).get("more_body", False):
            continue
        await send(
            cast(
                "Message",
                {"type": "http.response.start", "status": 200, "headers": headers},
            )
        )
        await send(cast("Message", {"type": "http.response.body", "body": body}))

    return _app


async def _drive(app: ASGIApp, scope: Scope, request_body: bytes) -> None:
    """Push one request through *app*."""

    async def _receive() -> ReceiveMessage:
        return cast(
            "HTTPRequestEvent",
            {"type": "http.request", "body": request_body, "more_body": False},
        )

    async def _send(message: Message) -> None:
        return None

    await app(scope, _receive, _send)


def _recorded(path: Path) -> list[dict[str, object]]:
    """Read back the transcript lines.

    Returns:
        The recorded exchanges.
    """
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


@pytest.mark.parametrize(
    ("encoding", "compress"),
    [
        ("br", brotli.compress),
        ("gzip", gzip.compress),
        ("deflate", zlib.compress),
        (None, bytes),
        ("identity", bytes),
    ],
)
async def test_compressed_response_is_recorded_as_the_client_reads_it(
    tmp_path: Path, encoding: str | None, compress: Callable[[bytes], bytes]
) -> None:
    """Every encoding the host may negotiate round-trips to the parsed body."""
    recorder = TranscriptRecorder()
    recorder.bind(tmp_path / "transcript.jsonl")
    body = compress(json.dumps(_RESPONSE_BODY).encode())

    await _drive(
        transcribing(_app_sending(body, encoding=encoding), recorder),
        _scope("/v1/chat/completions"),
        b'{"model": "example-large-001"}',
    )

    entry = _recorded(tmp_path / "transcript.jsonl")[0]
    assert entry["response"] == _RESPONSE_BODY
    assert entry["request"] == {"model": "example-large-001"}


async def test_streamed_body_is_kept_as_text(tmp_path: Path) -> None:
    """A server-sent-events completion is evidence even though it is not JSON."""
    recorder = TranscriptRecorder()
    recorder.bind(tmp_path / "transcript.jsonl")

    await _drive(
        transcribing(
            _app_sending(b'data: {"delta": "hi"}\n\ndata: [DONE]\n\n', encoding=None),
            recorder,
        ),
        _scope("/v1/chat/completions"),
        b"{}",
    )

    assert "[DONE]" in str(_recorded(tmp_path / "transcript.jsonl")[0]["response"])


async def test_undecodable_body_is_recorded_without_losing_the_exchange(
    tmp_path: Path,
) -> None:
    """A body that does not decode still yields a line naming what it was.

    A dropped exchange is a hole in the evidence with nothing to say it is
    there, which is worse than a line an operator can see is unreadable.
    """
    recorder = TranscriptRecorder()
    recorder.bind(tmp_path / "transcript.jsonl")

    truncated = gzip.compress(json.dumps(_RESPONSE_BODY).encode())[:20]

    await _drive(
        transcribing(_app_sending(truncated, encoding="gzip"), recorder),
        _scope("/v1/chat/completions"),
        b"{}",
    )

    response = str(_recorded(tmp_path / "transcript.jsonl")[0]["response"])
    assert response == f"<undecodable gzip body, {len(truncated)} bytes>"


async def test_authorization_header_is_never_recorded(tmp_path: Path) -> None:
    """The bearer this process mints stays out of a file an operator opens."""
    recorder = TranscriptRecorder()
    recorder.bind(tmp_path / "transcript.jsonl")

    await _drive(
        transcribing(
            _app_sending(json.dumps(_RESPONSE_BODY).encode(), encoding=None), recorder
        ),
        _scope("/v1/chat/completions"),
        b"{}",
    )

    assert _BEARER not in (tmp_path / "transcript.jsonl").read_text("utf-8")


async def test_non_completion_traffic_is_not_recorded(tmp_path: Path) -> None:
    """Only completion traffic is transcribed; the rest is the recorder itself."""
    recorder = TranscriptRecorder()
    recorder.bind(tmp_path / "transcript.jsonl")

    await _drive(
        transcribing(_app_sending(b'{"ok": true}', encoding=None), recorder),
        _scope("/health"),
        b"",
    )

    assert not (tmp_path / "transcript.jsonl").exists()


async def test_unbound_recorder_records_nothing(tmp_path: Path) -> None:
    """The host serves the same application whether or not anybody transcribes."""
    recorder = TranscriptRecorder()
    recorder.bind(tmp_path / "transcript.jsonl")
    recorder.unbind()

    await _drive(
        transcribing(
            _app_sending(json.dumps(_RESPONSE_BODY).encode(), encoding=None), recorder
        ),
        _scope("/v1/chat/completions"),
        b"{}",
    )

    assert not (tmp_path / "transcript.jsonl").exists()
