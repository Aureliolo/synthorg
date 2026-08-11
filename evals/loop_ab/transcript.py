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

The Authorization header is never read here. The bearer is the one credential
this process mints, and a transcript is a file an operator opens.
"""

import json
from pathlib import Path
from typing import Final

from litestar.enums import ScopeType
from litestar.types import ASGIApp, Message, Receive, ReceiveMessage, Scope, Send

from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_LOOP_AB_TRANSCRIPT_WRITE_FAILED

logger = get_logger(__name__)

#: Only completion traffic is transcribed. The MCP endpoint carries tool calls
#: whose results are already visible in the workspace, and everything else on
#: this host is the recorder talking to itself.
_COMPLETIONS_PATH: Final[str] = "/chat/completions"


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

        async def _receive() -> ReceiveMessage:
            message = await receive()
            if message["type"] == "http.request":
                request_chunks.append(bytes(message.get("body", b"")))
            return message

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.body":
                response_chunks.append(bytes(message.get("body", b"")))
            await send(message)

        try:
            await app(scope, _receive, _send)
        finally:
            recorder.write(
                {
                    "request": _decode(b"".join(request_chunks)),
                    "response": _decode(b"".join(response_chunks)),
                }
            )

    return _wrapped


def _decode(payload: bytes) -> object:
    """Decode a captured body, preferring JSON.

    A streamed completion is server-sent events rather than one JSON document,
    so the raw text is kept when it does not parse: an unreadable transcript
    line is still evidence, and a dropped one is not.

    Returns:
        The parsed body, or its text when it is not a JSON document.
    """
    if not payload:
        return None
    text = payload.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


__all__ = ["ASGIApp", "TranscriptRecorder", "transcribing"]
