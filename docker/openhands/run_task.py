"""In-container OpenHands task runner (image-only entry point).

Runs one OpenHands agent task fully inside the sandbox container: the SDK's
native ``terminal`` / ``file_editor`` tools execute here, never on the host.
The agent reaches models only through the LLM gateway (its OpenAI base URL)
and credentialed tools only through the credentialed-MCP endpoint; the
container's egress is pinned to those two hosts by the sidecar.

Transport is deliberately minimal: the run spec arrives as one JSON line on
stdin, and each agent event is emitted as one normalized JSON line on stdout
for the host-side adapter (``engine/openhands/container_runtime.py``) to parse.
Reading a single line (rather than to EOF) lets the host keep the attached
stdin open for the container's lifetime without a half-close dance, so the
adapter can tear the container down the instant a boundary check trips. This
keeps ``openhands-sdk`` out of the main application venv entirely.

This module runs only inside ``docker/openhands`` (which bundles
``openhands-sdk`` + ``openhands-tools``); it is never imported by the app.
"""

import io
import json
import os
import sys
import traceback
from pathlib import PurePosixPath
from typing import TextIO, cast, override
from uuid import UUID

_REDACTED = "***"


class _SecretScrubbingStream(io.TextIOBase):
    """A text stream that masks the run bearer in everything written to it.

    Everything the SDK's dependency closure writes to stderr reaches the
    host, which logs it into the app's structured pipeline. The host's
    pattern-based scrubber cannot recognise this token (a quoted value in a
    Python repr matches none of its shapes), and the container is the only
    place that knows it verbatim, so the mask belongs here, on the way out.
    Masking per write is sound for line-oriented writers; a token deliberately
    split across two ``write`` calls would survive, which is why the token
    never reaches this stream from our own code.
    """

    def __init__(self, wrapped: TextIO) -> None:
        super().__init__()
        self._wrapped = wrapped
        self._secret = ""

    def bind_secret(self, secret: str) -> None:
        """Start masking *secret* in subsequent writes."""
        self._secret = secret

    def scrub(self, text: str) -> str:
        """Mask the bound secret in *text*.

        Returns:
            *text* with every occurrence of the bound secret replaced.
        """
        return text.replace(self._secret, _REDACTED) if self._secret else text

    @override
    def write(self, s: str, /) -> int:
        """Write *s* with the bound secret masked.

        Returns:
            The number of characters accepted.
        """
        self._wrapped.write(self.scrub(s))
        return len(s)

    @override
    def flush(self) -> None:
        """Flush the wrapped stream."""
        self._wrapped.flush()

    @override
    def fileno(self) -> int:
        """Return the wrapped stream's descriptor.

        Returns:
            The underlying file descriptor.
        """
        return self._wrapped.fileno()

    @override
    def writable(self) -> bool:
        """Report the stream as writable.

        Returns:
            Always ``True``.
        """
        return True


# Claim the real stdout as the private event channel, then point BOTH
# ``sys.stdout`` and ``sys.stderr`` at a scrubbing wrapper over stderr, BEFORE
# importing the SDK. stdout is a parsed protocol: the host reads one JSON event
# per line, and the SDK's console visualizer or any stray print in its large
# dependency closure would interleave prose with those lines. This covers every
# Python-level writer; a spawned tool writing straight to fd 1 is not rerouted
# by it, which is what the container contract test exercises against the real
# image. The scrubbing wrapper is the single chokepoint that keeps the run
# bearer out of the host's log sink no matter which library does the writing.
_EVENTS = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
_DIAGNOSTICS = _SecretScrubbingStream(sys.stderr)
sys.stderr = cast("TextIO", _DIAGNOSTICS)
sys.stdout = cast("TextIO", _DIAGNOSTICS)

# The tools import is load-bearing: it registers the ``terminal`` /
# ``file_editor`` executors into the SDK tool registry by import side effect.
import openhands.tools  # noqa: E402, F401
from openhands.sdk import LLM, Agent, Conversation, Tool  # noqa: E402
from openhands.sdk.event import (  # noqa: E402
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.mcp.config import MCPServer  # noqa: E402

_LLM_USAGE_ID = "openhands-loop"
_NATIVE_TOOLS = ("terminal", "file_editor")

# Latch so each missing-metrics-shape diagnostic is written once per run.
_SHAPE_REPORTED: set[str] = set()

# SDK events the adapter deliberately does not forward: lifecycle, streaming
# and telemetry that carry no turn, no action and no cost. Matched by class
# NAME rather than isinstance so the container never fails to start because
# the SDK dropped one, and so a RENAMED event falls through to "unmapped"
# instead of being silently absorbed. Naming them is what lets anything
# outside the set mean "the SDK grew something the adapter should map".
_IGNORED_EVENT_NAMES: frozenset[str] = frozenset(
    {
        "ACPToolCallEvent",
        "CondensationSummaryEvent",
        "ConversationStateUpdateEvent",
        "HookExecutionEvent",
        "InterruptEvent",
        "LLMCompletionLogEvent",
        "PauseEvent",
        "StreamingDeltaEvent",
        "SystemPromptEvent",
        "TokenEvent",
    }
)

# LiteLLM routes by a provider prefix on the model name, and the SDK hands
# ``model`` to it verbatim (``litellm_call_kwargs`` returns it unchanged for
# any non-OpenHands model), so an unprefixed SynthOrg model id resolves to no
# provider and the call fails before it reaches ``base_url``. This prefix names
# the WIRE PROTOCOL, not a vendor: it means "an OpenAI-compatible proxy at
# api_base", which is exactly what the SynthOrg gateway is. The gateway binds
# the real (provider, model) from the run bearer's claims and ignores the
# request's model field entirely, so nothing here selects a vendor.
_PROXY_MODEL_PREFIX = "litellm_proxy/"


def _routed_model(model: str) -> str:
    """Prefix a model id so LiteLLM routes it to the gateway's base URL.

    The test is the proxy prefix itself, never "does this id contain a
    slash": catalog ids routinely carry a vendor-shaped namespace, and
    treating one as already-routed sends the call to that vendor's real
    endpoint instead of the gateway, escaping the run's binding and budget.

    Returns:
        The model id carrying an OpenAI-compatible proxy provider prefix.
    """
    if model.startswith(_PROXY_MODEL_PREFIX):
        return model
    return f"{_PROXY_MODEL_PREFIX}{model}"


def _emit(payload: dict[str, object]) -> None:
    """Write one normalized event as a JSON line to the event channel."""
    _EVENTS.write(json.dumps(payload, separators=(",", ":")) + "\n")
    _EVENTS.flush()


def _safe_error_text(exc: Exception) -> str:
    """Render an exception for emission with the run bearer scrubbed out.

    The container holds the per-run gateway token (used as the LLM api key and
    as the MCP ``Authorization`` header), and an HTTP/SDK failure can echo a
    request URL or header into its message. That text is written to stdout,
    which the host parses, logs, and can surface in turn content, so the token
    is redacted at this boundary (mirroring the app's SEC-1 log-redaction
    rule, which this image-only module cannot import).

    Returns:
        ``"<ErrorType>: <message>"`` with the run bearer masked.
    """
    return _DIAGNOSTICS.scrub(f"{type(exc).__name__}: {exc}")


def _text_of(event: object) -> str:
    """Best-effort human-readable text for an event.

    Returns:
        The event's text content, or an empty string.
    """
    for attr in ("error", "thought"):
        value = getattr(event, attr, None)
        if isinstance(value, str) and value:
            return value
    message = getattr(event, "llm_message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [getattr(p, "text", "") for p in content]
        return "".join(t for t in parts if isinstance(t, str))
    return ""


def _normalize(event: object) -> dict[str, object] | None:
    """Map one SDK event onto the adapter's normalized JSON shape.

    An event that is neither adapter-relevant nor in the known-ignored set is
    reported as ``unmapped`` carrying its class name: the host logs that at
    WARNING, so an SDK upgrade that introduces or renames an event surfaces as
    a visible protocol skew rather than a silently missing turn.

    Returns:
        The normalized event dict, or ``None`` for a known-ignored event.
    """
    turn = _normalize_turn(event)
    if turn is not None:
        return turn
    name = type(event).__name__
    if name in _IGNORED_EVENT_NAMES:
        return None
    return {"kind": "unmapped", "text": name}


def _normalize_turn(event: object) -> dict[str, object] | None:
    """Map one adapter-relevant SDK event onto its normalized shape.

    Returns:
        The normalized event dict, or ``None`` for any other event class.
    """
    if isinstance(event, ActionEvent):
        return {
            "kind": "action",
            "text": _text_of(event),
            "tool_name": getattr(event, "tool_name", None),
        }
    if isinstance(event, ObservationEvent):
        return {"kind": "observation", "tool_name": getattr(event, "tool_name", None)}
    if isinstance(event, MessageEvent):
        return {"kind": "message", "text": _text_of(event)}
    if isinstance(event, AgentErrorEvent):
        # Scrubbed like every other outbound text: an SDK error carries
        # request context, and this path never reaches _safe_error_text.
        return {"kind": "error", "text": _DIAGNOSTICS.scrub(_text_of(event))}
    return None


def _build_agent(spec: dict[str, object]) -> Agent:
    """Build the agent bound to the gateway LLM and credentialed-MCP tools.

    Returns:
        The configured SDK ``Agent``.
    """
    llm = LLM(
        model=_routed_model(str(spec["model"])),
        api_key=spec["gateway_token"],
        base_url=spec["gateway_base_url"],
        usage_id=_LLM_USAGE_ID,
    )
    mcp = MCPServer(
        url=f"{spec['mcp_base_url']}/mcp",
        transport="streamable-http",
        headers={"Authorization": f"Bearer {spec['gateway_token']}"},
    )
    return Agent(
        llm=llm,
        tools=[Tool(name=name) for name in _NATIVE_TOOLS],
        mcp_config={"credentialed": mcp},
    )


def _combined_metrics(conversation: object) -> object | None:
    """Read the conversation's combined metrics object, best-effort.

    The run totals live on the COMBINED metrics, not on ``ConversationStats``
    itself (the SDK reads them the same way for its own budget check), so
    reaching for the attributes directly silently yields zero on every event.

    Returns:
        The combined metrics object, or ``None`` when unavailable.
    """
    stats = getattr(conversation, "conversation_stats", None)
    combined = getattr(stats, "get_combined_metrics", None)
    return combined() if callable(combined) else None


def _report_shape_once(key: str, detail: str) -> None:
    """Write a missing-metrics-shape diagnostic once per run per *key*.

    Zero is indistinguishable from "this run spent nothing", and the host reads
    these numbers for its budget kill and for the A/B token ranking, so a shape
    the contract test does not reproduce has to leave a trace rather than
    silently disarming either. Once per run: this runs on every event, and a
    broken shape stays broken for the whole run, so repeating it would bury the
    rest of the container's diagnostics.
    """
    if key in _SHAPE_REPORTED:
        return
    _SHAPE_REPORTED.add(key)
    sys.stderr.write(f"{key} unavailable: {detail}\n")


def _accumulated_cost(conversation: object) -> float:
    """Read the conversation's running accumulated cost, best-effort.

    Returns:
        The accumulated cost, or ``0.0`` when unavailable.
    """
    metrics = _combined_metrics(conversation)
    cost = getattr(metrics, "accumulated_cost", None)
    if cost is None:
        _report_shape_once("accumulated cost", f"metrics={type(metrics).__name__}")
        return 0.0
    return float(cost or 0.0)


def _accumulated_tokens(conversation: object) -> tuple[int, int]:
    """Read the conversation's running accumulated token usage, best-effort.

    The host's A/B rubric ranks loops on tokens and treats a zero as
    unbeatable, so a run reporting none would win that dimension by reporting
    nothing at all. Emitting the totals is what makes the comparison real.

    Returns:
        ``(prompt_tokens, completion_tokens)``, zeroed when unavailable.
    """
    metrics = _combined_metrics(conversation)
    usage = getattr(metrics, "accumulated_token_usage", None)
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    if prompt is None and completion is None:
        _report_shape_once(
            "accumulated tokens",
            f"metrics={type(metrics).__name__} usage={type(usage).__name__}",
        )
        return 0, 0
    return int(prompt or 0), int(completion or 0)


def _totals(conversation: object) -> dict[str, object]:
    """Return the running accumulated figures to stamp on one event.

    Returns:
        The ``cost`` / ``input_tokens`` / ``output_tokens`` run totals.
    """
    prompt_tokens, completion_tokens = _accumulated_tokens(conversation)
    return {
        "cost": _accumulated_cost(conversation),
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
    }


def _run(spec: dict[str, object]) -> None:
    """Run one agent task, streaming normalized events, then a terminal line.

    Each emitted event carries the conversation's running accumulated cost and
    token usage so the host adapter can attribute per-turn deltas; the host is
    the authoritative cost sink (via the gateway), this is a reconciling
    signal.
    """
    agent = _build_agent(spec)
    holder: dict[str, object] = {}

    def _callback(event: object) -> None:
        normalized = _normalize(event)
        if normalized is None:
            return
        conversation = holder.get("conversation")
        if conversation is not None:
            normalized.update(_totals(conversation))
        _emit(normalized)

    # Persist conversation state under the (rw) workspace keyed by a stable
    # per-task conversation id, so a resumed run re-attaches to the prior
    # conversation rather than starting cold.
    workspace = str(spec["workspace_path"])
    persistence_dir = str(PurePosixPath(workspace) / ".openhands")
    conversation = Conversation(
        agent=agent,
        workspace=workspace,
        persistence_dir=persistence_dir,
        conversation_id=UUID(str(spec["conversation_id"])),
        max_iteration_per_run=int(str(spec["max_turns"])),
        callbacks=[_callback],
        visualizer=None,
    )
    holder["conversation"] = conversation
    conversation.send_message(spec["task_prompt"])
    conversation.run()
    _emit({"kind": "finished", **_totals(conversation)})


def main() -> int:
    """Read the run spec line from stdin, run the task, return an exit code.

    Returns:
        ``0`` on success, ``1`` on any failure (reported as an error line).
    """
    try:
        line = sys.stdin.readline()
        if not line.strip():
            _emit({"kind": "error", "text": "empty run spec on stdin"})
            return 1
        spec = json.loads(line)
        # Bind before the first SDK call, so every subsequent diagnostic
        # write from any library is masked on its way to the host.
        if isinstance(spec, dict):
            _DIAGNOSTICS.bind_secret(str(spec.get("gateway_token", "")))
        _run(spec)
    except Exception as exc:  # noqa: BLE001 -- container boundary: report, don't crash silently
        # The emitted line carries only "<Type>: <message>"; without the
        # traceback a rare failure leaves nothing to debug from. It goes to
        # the scrubbing stream, which masks a bearer echoed by a frame.
        sys.stderr.write(traceback.format_exc())
        _emit({"kind": "error", "text": _safe_error_text(exc)})
        return 1
    return 0


if __name__ == "__main__":
    with _EVENTS:
        raise SystemExit(main())
