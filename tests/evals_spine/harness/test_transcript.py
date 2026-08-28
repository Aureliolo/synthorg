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

from evals.harness.transcript import TranscriptRecorder, transcribing

pytestmark = pytest.mark.unit

_BEARER: Final = "secret-bearer-value"

_RESPONSE_BODY: Final[dict[str, object]] = {
    "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 4096, "completion_tokens": 128},
}


_LABEL: Final = "d1-gated-r0-leaf-one"


def _bound(recorder: TranscriptRecorder, path: Path, *, bearer: str = _BEARER) -> None:
    """Bind *path* the way a session does: a label, and the token that reaches it.

    Args:
        recorder: The recorder under test.
        path: Where this session's exchanges land.
        bearer: The credential its requests present.
    """
    recorder.bind(_LABEL, path)
    recorder.attach(bearer, _LABEL)


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
    _bound(recorder, tmp_path / "transcript.jsonl")
    body = compress(json.dumps(_RESPONSE_BODY).encode())

    await _drive(
        transcribing(_app_sending(body, encoding=encoding), recorder),
        _scope("/v1/chat/completions"),
        b'{"model": "example-expert-001"}',
    )

    entry = _recorded(tmp_path / "transcript.jsonl")[0]
    assert entry["response"] == _RESPONSE_BODY
    assert entry["request"] == {"model": "example-expert-001"}


async def test_streamed_body_is_kept_as_text(tmp_path: Path) -> None:
    """A server-sent-events completion is evidence even though it is not JSON."""
    recorder = TranscriptRecorder()
    _bound(recorder, tmp_path / "transcript.jsonl")

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
    _bound(recorder, tmp_path / "transcript.jsonl")

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
    _bound(recorder, tmp_path / "transcript.jsonl")

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
    _bound(recorder, tmp_path / "transcript.jsonl")

    await _drive(
        transcribing(_app_sending(b'{"ok": true}', encoding=None), recorder),
        _scope("/health"),
        b"",
    )

    assert not (tmp_path / "transcript.jsonl").exists()


async def test_unbound_recorder_records_nothing(tmp_path: Path) -> None:
    """The host serves the same application whether or not anybody transcribes."""
    recorder = TranscriptRecorder()
    _bound(recorder, tmp_path / "transcript.jsonl")
    recorder.unbind(_LABEL)

    await _drive(
        transcribing(
            _app_sending(json.dumps(_RESPONSE_BODY).encode(), encoding=None), recorder
        ),
        _scope("/v1/chat/completions"),
        b"{}",
    )

    assert not (tmp_path / "transcript.jsonl").exists()


async def test_a_straggler_lands_in_the_cell_that_issued_it(tmp_path: Path) -> None:
    """An exchange belongs to the repetition that started it, not the one that
    happened to be bound when it finished.

    The runner rebinds between repetitions, so reading the destination at the
    end would file a slow completion under the next cell, or drop it when the
    matrix has moved on: evidence that is silently wrong either way.
    """
    recorder = TranscriptRecorder()
    first = tmp_path / "rep1.jsonl"
    second = tmp_path / "rep2.jsonl"
    _bound(recorder, first)

    async def _rebinding_app(scope: Scope, receive: Receive, send: Send) -> None:
        # Stands in for the repetition ending while this call is in flight.
        recorder.bind(_LABEL, second)
        await _app_sending(json.dumps(_RESPONSE_BODY).encode(), encoding=None)(
            scope, receive, send
        )

    await _drive(
        transcribing(_rebinding_app, recorder), _scope("/v1/chat/completions"), b"{}"
    )

    assert len(_recorded(first)) == 1
    assert not second.exists()


async def test_a_credential_in_a_body_is_scrubbed(tmp_path: Path) -> None:
    """The bodies are whole payloads, and a transcript gets forwarded."""
    recorder = TranscriptRecorder()
    _bound(recorder, tmp_path / "transcript.jsonl")
    leaked = "sk-not-a-real-key-0123456789"

    await _drive(
        transcribing(
            _app_sending(json.dumps({"api_key": leaked}).encode(), encoding=None),
            recorder,
        ),
        _scope("/v1/chat/completions"),
        json.dumps({"messages": [{"content": f"my api_key is {leaked}"}]}).encode(),
    )

    assert leaked not in (tmp_path / "transcript.jsonl").read_text("utf-8")


def _scope_for(bearer: str) -> Scope:
    """A completions scope presenting *bearer*.

    Returns:
        The scope.
    """
    return cast(
        "HTTPScope",
        {
            "type": "http",
            "path": "/v1/chat/completions",
            "method": "POST",
            "headers": [(b"authorization", f"Bearer {bearer}".encode())],
        },
    )


async def test_concurrent_sessions_do_not_take_each_others_transcripts(
    tmp_path: Path,
) -> None:
    """The defect this keying exists for, stated as a test.

    A recorder holding one current path records whichever session bound last.
    Measured on a live cell: three of eight leaves produced no transcript at
    all, and one file named for a single leaf held requests from four units.
    """
    recorder = TranscriptRecorder()
    first = tmp_path / "leaf-one.jsonl"
    second = tmp_path / "leaf-two.jsonl"
    recorder.bind("leaf-one", first)
    recorder.attach("bearer-one", "leaf-one")
    # The sibling binds while the first is still open, which is exactly what
    # concurrency does and what used to take the first one's path away.
    recorder.bind("leaf-two", second)
    recorder.attach("bearer-two", "leaf-two")

    app = transcribing(
        _app_sending(json.dumps(_RESPONSE_BODY).encode(), encoding=None), recorder
    )
    await _drive(app, _scope_for("bearer-one"), b'{"unit": "one"}')
    await _drive(app, _scope_for("bearer-two"), b'{"unit": "two"}')

    assert len(_recorded(first)) == 1
    assert len(_recorded(second)) == 1


async def test_one_session_ending_does_not_blind_its_sibling(
    tmp_path: Path,
) -> None:
    """An unbind names one session, so the rest keep recording.

    Unscoped, the first leaf of a concurrent wave to finish stopped every
    transcript still open, and the siblings' remaining turns went nowhere.
    """
    recorder = TranscriptRecorder()
    surviving = tmp_path / "leaf-two.jsonl"
    recorder.bind("leaf-one", tmp_path / "leaf-one.jsonl")
    recorder.attach("bearer-one", "leaf-one")
    recorder.bind("leaf-two", surviving)
    recorder.attach("bearer-two", "leaf-two")

    recorder.unbind("leaf-one")

    await _drive(
        transcribing(
            _app_sending(json.dumps(_RESPONSE_BODY).encode(), encoding=None), recorder
        ),
        _scope_for("bearer-two"),
        b'{"unit": "two"}',
    )

    assert len(_recorded(surviving)) == 1


async def test_a_request_with_no_known_bearer_records_nothing(
    tmp_path: Path,
) -> None:
    """A token nobody attached belongs to no recorded session."""
    recorder = TranscriptRecorder()
    path = tmp_path / "leaf-one.jsonl"
    recorder.bind("leaf-one", path)
    recorder.attach("bearer-one", "leaf-one")

    await _drive(
        transcribing(
            _app_sending(json.dumps(_RESPONSE_BODY).encode(), encoding=None), recorder
        ),
        _scope_for("some-other-token"),
        b"{}",
    )

    assert not path.exists()
