# module-kind: code
"""Record what a run actually said, at the one place it is observable.

A scoreboard says which cell scored better. It cannot say whether the agent
thought before acting, argued with itself, restated the task four times, or
drifted off the brief, and those are the differences an operator asks about
first.

The loop cannot be read for that directly: its messages live in its own
context and never reach this process. What it does do is dial this host's LLM
gateway for every turn, so every prompt and completion passes through one ASGI
application on its way out and back.

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
this process mints, and a transcript is a file an operator opens. What is
recorded still goes through the same secret scrubber the logs use, because the
bodies are whole payloads and structural safety today is not a reason to write
an unscrubbed one tomorrow.
"""

import asyncio
import gzip
import json
import zlib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Final

import brotli
from litestar.enums import ScopeType
from litestar.types import ASGIApp, Message, Receive, ReceiveMessage, Scope, Send

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evals import EVALS_HARNESS_TRANSCRIPT_WRITE_FAILED
from synthorg.observability.redaction import scrub_secret_tokens

logger = get_logger(__name__)

#: Only completion traffic is transcribed. The MCP endpoint carries tool calls
#: whose results are already visible in the workspace, and everything else on
#: this host is the recorder talking to itself.
_COMPLETIONS_PATH: Final[str] = "/chat/completions"

#: Header naming the encoding applied on the way out, lower-cased by ASGI.
_CONTENT_ENCODING: Final[bytes] = b"content-encoding"

#: Header carrying the per-session bearer, which is how a request says which
#: transcript it belongs to.
_AUTHORIZATION: Final[bytes] = b"authorization"

#: Encodings that carry the body unchanged.
_IDENTITY_ENCODINGS: Final[frozenset[str]] = frozenset({"", "identity"})


class TranscriptRecorder:
    """Collects one JSONL transcript per session, keyed by that session's bearer.

    Sessions run concurrently, so which transcript a request belongs to is a
    property of the REQUEST, never of whichever session bound last. A single
    bound path was the original design, correct while units ran one at a time;
    at ``--leaf-concurrency 4`` each bind stole the path from whoever held it
    and each unbind blinded the rest. Measured on one recorded cell: three of
    eight leaves produced no transcript at all, and one file named for a single
    leaf held requests from four different units.

    The bearer is the only session identity the ASGI tap can see. It is minted
    per session and carries the execution id in its claims, but the tap runs
    ahead of the gateway that verifies it, so the token is matched as an opaque
    string rather than decoded.

    Unbound, it records nothing: the host serves the same application whether
    or not anybody is transcribing.
    """

    def __init__(self) -> None:
        """Start with nothing bound, recording nothing."""
        self._paths: dict[str, Path] = {}
        self._bearers: dict[str, str] = {}

    def bind(self, label: str, path: Path) -> None:
        """Send *label*'s exchanges to *path*.

        Args:
            label: The session's execution id.
            path: JSONL file to append to; parents are created.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        self._paths[label] = path

    def attach(self, bearer: str, label: str) -> None:
        """Record that *bearer* is how *label*'s requests identify themselves.

        Written where the token is minted, which is the one place both facts
        are in hand. Kept apart from :meth:`bind` because the two are owned by
        different callers: a session knows which file is its own, and the
        binder knows which credential it handed that session.

        Args:
            bearer: The signed gateway token for the session.
            label: The session's execution id.
        """
        self._bearers[bearer] = label

    def unbind(self, label: str) -> None:
        """Stop recording *label*, and forget the bearer that reached it.

        Args:
            label: The session's execution id.
        """
        self._paths.pop(label, None)
        for bearer, bound in list(self._bearers.items()):
            if bound == label:
                del self._bearers[bearer]

    def path_for(self, bearer: str | None) -> Path | None:
        """The transcript a request carrying *bearer* belongs to.

        Read at the START of an exchange, not at its end: a completion that
        outlives the session that issued it would otherwise be appended to
        whichever session happened to be bound when it finished, or dropped
        entirely if none was. Either way the evidence is silently wrong.

        Args:
            bearer: The token the request presented, or ``None``.

        Returns:
            The bound path, or ``None`` when this request belongs to no
            recorded session.
        """
        if bearer is None:
            return None
        label = self._bearers.get(bearer)
        if label is None:
            return None
        return self._paths.get(label)

    @staticmethod
    def write(entry: dict[str, object], path: Path) -> None:
        """Append one exchange to *path*.

        A transcript is a diagnostic, so a write failure must never take down
        the run that was producing it. Serialisation uses ``default=str`` so an
        unexpected object shape degrades to its text rather than raising, and
        the line is scrubbed before it lands: the bodies are whole request and
        response payloads, and a transcript is a file an operator opens and
        forwards.

        Blocking, and called through a worker thread: it runs on the ASGI
        path, where a synchronous write would stall the event loop this same
        process is serving both legs from.

        Args:
            entry: The exchange to record.
            path: The transcript the exchange belongs to.
        """
        try:
            body = json.dumps(entry, ensure_ascii=False, default=str)
            line = scrub_secret_tokens(body)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(
                EVALS_HARNESS_TRANSCRIPT_WRITE_FAILED,
                path=str(path),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


def _is_completion(scope: Scope) -> bool:
    """Whether *scope* is the completion traffic this tap records.

    Compared by value, not identity: ASGI puts a plain ``str`` in the scope and
    ``ScopeType`` is a string enum, so ``is`` never matches and every request
    would slip past untranscribed.

    Returns:
        ``True`` for an HTTP request to the completions path.
    """
    return scope["type"] == ScopeType.HTTP and _COMPLETIONS_PATH in str(
        scope.get("path", "")
    )


def _bearer_of(scope: Scope) -> str | None:
    """Read the presented bearer out of a raw ASGI scope.

    The tap runs ahead of the gateway that verifies the token, so this matches
    the credential as an opaque string and never parses it. A request with no
    bearer belongs to no recorded session, which is the same answer as a
    request whose session is not being transcribed.

    Returns:
        The token, or ``None`` when the request presents none.
    """
    for name, value in scope.get("headers", ()):
        if name.lower() != _AUTHORIZATION:
            continue
        presented = value.decode("latin-1", errors="replace").strip()
        scheme, _, token = presented.partition(" ")
        if scheme.lower() == "bearer" and token:
            return token.strip()
    return None


def transcribing(app: ASGIApp, recorder: TranscriptRecorder) -> ASGIApp:
    """Wrap *app* so gateway completions are teed to *recorder*.

    Returns:
        An ASGI application delegating to *app* and recording as it goes.
    """

    async def _wrapped(scope: Scope, receive: Receive, send: Send) -> None:
        path = recorder.path_for(_bearer_of(scope)) if _is_completion(scope) else None
        if path is None:
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
            entry: dict[str, object] = {
                "request": _decode(b"".join(request_chunks)),
                "response": _decode(
                    b"".join(response_chunks), encoding=response_encoding
                ),
            }
            await asyncio.to_thread(recorder.write, entry, path)

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
