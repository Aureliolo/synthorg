# module-kind: adapter
"""Real ``openhands-sdk`` conversation factory (sandbox-image only).

The SDK and its ``agent_server`` are bundled only in the OpenHands sandbox
image, never the main package venv, so the ``openhands`` import here is lazy
and guarded: on a host without the SDK it raises
:class:`OpenHandsUnavailableError` (fail loud) rather than importing a
missing dependency. This keeps the litellm / pyo3-3.14 pin holds out of the
main venv entirely.

This module is exercised end-to-end by the docker-gated live smoke, not the
unit tier: the unit tests drive the :class:`OpenHandsConversation` protocol
against a deterministic fake, so the loop logic is fully covered without the
SDK present.
"""

import asyncio
from typing import Any, Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.openhands.conversation import (
    EventSink,
    OpenHandsConversation,
    OpenHandsOutcome,
    OpenHandsRunSpec,
)
from synthorg.engine.openhands.errors import (
    OpenHandsRuntimeError,
    OpenHandsUnavailableError,
)
from synthorg.engine.openhands.events import OpenHandsEvent, OpenHandsEventKind

_NATIVE_TOOLS: Final[tuple[str, ...]] = ("BashTool", "FileEditorTool")
_LLM_SERVICE_ID: Final[str] = "openhands-loop"


def _require_sdk() -> Any:  # type: ignore[explicit-any]  # untyped image-only SDK
    """Import the ``openhands`` SDK, or fail loud when it is absent.

    The return is dynamically typed: ``openhands-sdk`` ships no type
    information and is absent from the main venv (image-only), so its module
    namespace is irreducibly ``Any`` at the one attribute-access site below.

    Returns:
        The imported ``openhands.sdk`` module namespace.

    Raises:
        OpenHandsUnavailableError: When ``openhands-sdk`` is not installed
            (the main venv, by design; the SDK lives only in the image).
    """
    try:
        from openhands import sdk  # noqa: PLC0415
    except ImportError as exc:
        msg = (
            "openhands-sdk is not installed; the OpenHands loop runs only in "
            "the sandbox image that bundles it"
        )
        raise OpenHandsUnavailableError(msg) from exc
    return sdk


def _normalize_event(event: object) -> OpenHandsEvent | None:
    """Map one SDK event onto a normalized :class:`OpenHandsEvent`.

    Token and cost fields are read best-effort from the SDK event's metrics;
    the authoritative cost is recorded host-side by the gateway's
    ``cost_recording_scope``, so a missing metric degrades to zero rather
    than failing the run.

    Args:
        event: A concrete ``openhands.sdk`` event instance.

    Returns:
        The normalized event, or ``None`` for SDK events that carry no
        adapter-relevant signal (skipped without terminating the run).
    """
    kind = _event_kind(event)
    if kind is None:
        return None
    usage = getattr(event, "llm_metrics", None) or getattr(event, "metrics", None)
    return OpenHandsEvent(
        kind=kind,
        text=_event_text(event),
        tool_name=getattr(event, "tool_name", None),
        input_tokens=_metric(usage, "prompt_tokens"),
        output_tokens=_metric(usage, "completion_tokens"),
        cost=float(getattr(usage, "accumulated_cost", 0.0) or 0.0),
    )


def _event_kind(event: object) -> OpenHandsEventKind | None:
    """Classify an SDK event by its class name.

    Returns:
        The matching kind, or ``None`` when the event is not adapter-relevant.
    """
    name = type(event).__name__
    if name == "ActionEvent":
        return OpenHandsEventKind.ACTION
    if name == "ObservationEvent":
        return OpenHandsEventKind.OBSERVATION
    if name == "MessageEvent":
        return OpenHandsEventKind.MESSAGE
    if name in ("AgentErrorEvent", "ErrorEvent"):
        return OpenHandsEventKind.ERROR
    return None


def _event_text(event: object) -> str:
    """Extract display text from an SDK event, tolerating missing fields.

    Returns:
        The event's text content, or an empty string.
    """
    for attr in ("error", "message", "content", "thought"):
        value = getattr(event, attr, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _metric(usage: object, field: str) -> int:
    """Read a non-negative integer token metric, defaulting to zero.

    Returns:
        The metric value clamped to ``>= 0``.
    """
    value = getattr(usage, field, 0) or 0
    return max(0, int(value))


class _SdkConversation:
    """Drives an ``openhands-sdk`` conversation, bridging its sync callback.

    The SDK delivers events through a synchronous callback while the adapter
    consumes an async sink that can request an early stop. We enqueue
    normalized events from the callback and drain them to the sink on the
    event loop, pausing the SDK run when the sink returns ``False``.
    """

    # Queued on the SDK's sync callback, drained to the async sink in run().
    _queue: asyncio.Queue[OpenHandsEvent | None]

    def __init__(self, conversation: Any, sink: EventSink) -> None:  # type: ignore[explicit-any]  # untyped image-only SDK conversation
        self._conversation = conversation
        self._sink = sink
        # Constructed and consumed on the same running loop within one run()
        # (never restarted across loops), so the loop binding is correct here.
        self._queue = asyncio.Queue()  # lint-allow: loop-bound-init -- same-loop object
        self._loop = asyncio.get_running_loop()
        self._stopped = False

    def on_event(self, event: object) -> None:
        """Enqueue a normalized event from the SDK's synchronous callback."""
        normalized = _normalize_event(event)
        if normalized is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, normalized)

    async def run(self) -> OpenHandsOutcome:
        """Run the SDK conversation, forwarding events until stop or finish.

        Returns:
            The terminal :class:`OpenHandsOutcome`.

        Raises:
            OpenHandsRuntimeError: When the SDK run fails mid-flight.
        """
        try:
            runner = asyncio.create_task(asyncio.to_thread(self._conversation.run))
            await self._drain(runner)
        except Exception as exc:
            reraise_critical(exc)
            msg = "OpenHands SDK run failed"
            raise OpenHandsRuntimeError(msg) from exc
        return OpenHandsOutcome(finished=not self._stopped)

    async def _drain(self, runner: asyncio.Task[None]) -> None:
        """Forward queued events to the sink until the run ends or is stopped."""
        # lint-allow: long-running-loop-kill-switch -- runner-end + sink-stop bounded
        while True:
            drained = await self._next_event(runner)
            if drained is None:
                return
            if not await self._sink(drained):
                self._stopped = True
                await asyncio.to_thread(self._conversation.pause)
                runner.cancel()
                return

    async def _next_event(self, runner: asyncio.Task[None]) -> OpenHandsEvent | None:
        """Await the next event, or ``None`` once the runner has finished.

        Returns:
            The next queued event, or ``None`` when the SDK run has ended and
            the queue is drained.
        """
        queue_get = asyncio.create_task(self._queue.get())
        done, _ = await asyncio.wait(
            {queue_get, runner}, return_when=asyncio.FIRST_COMPLETED
        )
        if queue_get in done:
            return queue_get.result()
        queue_get.cancel()
        if not self._queue.empty():
            return self._queue.get_nowait()
        return None


async def build_sdk_conversation(
    spec: OpenHandsRunSpec, sink: EventSink
) -> OpenHandsConversation:
    """Build a live ``openhands-sdk`` conversation for a run spec.

    Points the SDK ``LLM`` at the gateway (``base_url`` + per-run bearer),
    attaches the native bash/editor tools plus the credentialed-MCP tools,
    and mounts the project workspace. The returned conversation forwards its
    events to ``sink``.

    Args:
        spec: The run parameters (task, model, gateway/MCP endpoints, token).
        sink: The async event sink the conversation forwards events to.

    Returns:
        A conversation satisfying :class:`OpenHandsConversation`.

    Raises:
        OpenHandsUnavailableError: When the SDK is not installed.
        OpenHandsRuntimeError: When the SDK conversation cannot be built.
    """
    sdk = _require_sdk()
    try:
        llm = sdk.LLM(
            model=spec.model,
            api_key=spec.gateway_token,
            base_url=spec.gateway_base_url,
            service_id=_LLM_SERVICE_ID,
        )
        agent = sdk.Agent(
            llm=llm,
            tools=[sdk.Tool(name=name) for name in _NATIVE_TOOLS],
            mcp_config={
                "mcpServers": {
                    "credentialed": {
                        "url": f"{spec.mcp_base_url}/mcp",
                        "headers": {"Authorization": f"Bearer {spec.gateway_token}"},
                    }
                }
            },
        )
        conversation = sdk.Conversation(
            agent=agent,
            workspace=spec.workspace_path,
            max_iterations=spec.max_turns,
        )
        wrapper = _SdkConversation(conversation, sink)
        conversation.callbacks.append(wrapper.on_event)
        conversation.send_message(spec.task_prompt)
    except OpenHandsUnavailableError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        msg = "Failed to build OpenHands conversation"
        raise OpenHandsRuntimeError(msg) from exc
    return wrapper
