# module-kind: code
"""Record what each loop actually said, at the one place both are observable.

A scoreboard says which loop scored better. It cannot say whether one thought
before acting, argued with itself, restated the task four times, or drifted off
the brief, and those are the differences an operator asks about first.

Neither loop can be read for that directly. The native loop's messages live in
its own context and the OpenHands harness runs inside a container whose
reasoning never reaches this process at all. What both do is dial this host's
LLM gateway for every turn, so the prompts and completions of both legs pass
through one ASGI application on their way out and back.

So the tap sits there, wrapping the host's app rather than reaching into the
gateway: that boundary is governance and stays exactly as it ships. What is
recorded is the request body and the response body of each completion call,
which is the whole conversation as the model saw it.

Wrapping the whole application means observing the response after the
compression middleware has had it, and a completion carrying a written file in
its tool-call arguments clears the compression threshold easily. So the tap
undoes the content encoding rather than storing the wire bytes: those decode to
mojibake, which costs the transcript its token counts and its final answer
precisely on the turns that did the most work.

The Authorization header is never read here. The bearer is the one credential
this process mints, and a transcript is a file an operator opens.
"""

import gzip
import json
import zlib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Final

import brotli
from litestar.enums import ScopeType
from litestar.types import ASGIApp, Message, Receive, ReceiveMessage, Scope, Send

from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_LOOP_AB_TRANSCRIPT_WRITE_FAILED

logger = get_logger(__name__)

#: Only completion traffic is transcribed. The MCP endpoint carries tool calls
#: whose results are already visible in the workspace, and everything else on
#: this host is the recorder talking to itself.
_COMPLETIONS_PATH: Final[str] = "/chat/completions"

#: Header naming the encoding applied on the way out, lower-cased by ASGI.
_CONTENT_ENCODING: Final[bytes] = b"content-encoding"

#: Encodings that carry the body unchanged.
_IDENTITY_ENCODINGS: Final[frozenset[str]] = frozenset({"", "identity"})


class TranscriptRecorder:
    """Collects one JSONL transcript per bound cell.

    Bound and unbound around each repetition by the runner, so a line always
    belongs to the run that produced it. Unbound, it records nothing: the host
    serves the same application whether or not anybody is transcribing.
    """

    def __init__(self) -> None:
        """Start unbound, recording nothing."""
        self._path: Path | None = None

    def bind(self, path: Path) -> None:
        """Send subsequent exchanges to *path*.

        Args:
            path: JSONL file to append to; parents are created.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path

    def unbind(self) -> None:
        """Stop recording until the next bind."""
        self._path = None

    def write(self, entry: dict[str, object]) -> None:
        """Append one exchange to the bound transcript.

        A transcript is a diagnostic, so a write failure must never take down
        the run that was producing it.

        Args:
            entry: The exchange to record.
        """
        path = self._path
        if path is None:
            return
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning(
                EVALS_LOOP_AB_TRANSCRIPT_WRITE_FAILED,
                path=str(path),
                error_type=type(exc).__name__,
            )


def transcribing(app: ASGIApp, recorder: TranscriptRecorder) -> ASGIApp:
    """Wrap *app* so gateway completions are teed to *recorder*.

    Returns:
        An ASGI application delegating to *app* and recording as it goes.
    """

    async def _wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        # Compared by value, not identity: ASGI puts a plain ``str`` in the
        # scope and ``ScopeType`` is a string enum, so ``is`` never matches and
        # every request would slip past untranscribed.
        if scope["type"] != ScopeType.HTTP or _COMPLETIONS_PATH not in str(
            scope.get("path", "")
        ):
            await app(scope, receive, send)
            return

        request_chunks: list[bytes] = []
        response_chunks: list[bytes] = []
        response_encoding = ""

        async def _receive() -> ReceiveMessage:
            message = await receive()
            if message["type"] == "http.request":
                request_chunks.append(bytes(message.get("body", b"")))
            return message

        async def _send(message: Message) -> None:
            nonlocal response_encoding
            if message["type"] == "http.response.start":
                response_encoding = _encoding_of(message.get("headers", ()))
            if message["type"] == "http.response.body":
                response_chunks.append(bytes(message.get("body", b"")))
            await send(message)

        try:
            await app(scope, _receive, _send)
        finally:
            recorder.write(
                {
                    "request": _decode(b"".join(request_chunks)),
                    "response": _decode(
                        b"".join(response_chunks), encoding=response_encoding
                    ),
                }
            )

    return _wrapped


def _encoding_of(headers: Iterable[tuple[bytes, bytes]]) -> str:
    """Read the content encoding from raw ASGI response headers.

    Returns:
        The lower-cased encoding, or the empty string when the body is plain.
    """
    for name, value in headers:
        if name.lower() == _CONTENT_ENCODING:
            return value.decode("ascii", errors="replace").strip().lower()
    return ""


def _inflate(payload: bytes) -> bytes:
    """Inflate a ``deflate`` body.

    Returns:
        The inflated bytes.
    """
    # Servers disagree about whether deflate carries the zlib wrapper.
    try:
        return zlib.decompress(payload)
    except zlib.error:
        return zlib.decompress(payload, -zlib.MAX_WBITS)


#: Every encoding the host's compression middleware can negotiate.
_DECOMPRESSORS: Final[dict[str, Callable[[bytes], bytes]]] = {
    "br": brotli.decompress,
    "gzip": gzip.decompress,
    "deflate": _inflate,
}


def _decompress(payload: bytes, encoding: str) -> bytes | None:
    """Undo *encoding*.

    Returns:
        The original bytes, or ``None`` when they cannot be recovered.
    """
    if encoding in _IDENTITY_ENCODINGS:
        return payload
    decompressor = _DECOMPRESSORS.get(encoding)
    if decompressor is None:
        return None
    try:
        return bytes(decompressor(payload))
    except OSError, brotli.error, zlib.error, EOFError:
        return None


def _decode(payload: bytes, *, encoding: str = "") -> object:
    """Decode a captured body, preferring JSON.

    A streamed completion is server-sent events rather than one JSON document,
    so the raw text is kept when it does not parse: an unreadable transcript
    line is still evidence, and a dropped one is not. A body whose encoding is
    unknown or corrupt is named rather than stored, because storing it means
    storing mojibake that reads like a recording of nonsense.

    Returns:
        The parsed body, its text when it is not a JSON document, or a note
        naming the encoding that defeated it.
    """
    if not payload:
        return None
    decompressed = _decompress(payload, encoding)
    if decompressed is None:
        return f"<undecodable {encoding or 'identity'} body, {len(payload)} bytes>"
    text = decompressed.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


__all__ = ["ASGIApp", "TranscriptRecorder", "transcribing"]
