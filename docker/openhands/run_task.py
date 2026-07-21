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

import json
import sys
from pathlib import PurePosixPath
from uuid import UUID

# The tools import is load-bearing: it registers the ``terminal`` /
# ``file_editor`` executors into the SDK tool registry by import side effect.
import openhands.tools  # noqa: F401
from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.mcp.config import MCPServer

_LLM_USAGE_ID = "openhands-loop"
_NATIVE_TOOLS = ("terminal", "file_editor")


def _emit(payload: dict[str, object]) -> None:
    """Write one normalized event as a JSON line to stdout."""
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


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

    Returns:
        The normalized event dict, or ``None`` for a non-adapter event.
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
        return {"kind": "error", "text": _text_of(event)}
    return None


def _build_agent(spec: dict[str, object]) -> Agent:
    """Build the agent bound to the gateway LLM and credentialed-MCP tools.

    Returns:
        The configured SDK ``Agent``.
    """
    llm = LLM(
        model=spec["model"],
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


def _accumulated_cost(conversation: object) -> float:
    """Read the conversation's running accumulated cost, best-effort.

    Returns:
        The accumulated cost, or ``0.0`` when unavailable.
    """
    stats = getattr(conversation, "conversation_stats", None)
    return float(getattr(stats, "accumulated_cost", 0.0) or 0.0)


def _run(spec: dict[str, object]) -> None:
    """Run one agent task, streaming normalized events, then a terminal line.

    Each emitted event carries the conversation's running accumulated cost
    so the host adapter can attribute a per-turn cost delta; the host is
    the authoritative cost sink (via the gateway), this is a reconciling
    signal.
    """
    agent = _build_agent(spec)
    holder: dict[str, object] = {}

    def _callback(event: object) -> None:
        normalized = _normalize(event)
        if normalized is None:
            # An SDK event outside the four adapter-relevant classes is not
            # forwarded, but log it to stderr (the host captures container
            # stderr at DEBUG) so a new/unhandled event type is discoverable
            # rather than vanishing without a trace.
            sys.stderr.write(f"unrecognized SDK event: {type(event).__name__}\n")
            return
        conversation = holder.get("conversation")
        if conversation is not None:
            normalized["cost"] = _accumulated_cost(conversation)
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
    )
    holder["conversation"] = conversation
    conversation.send_message(spec["task_prompt"])
    conversation.run()
    _emit({"kind": "finished", "cost": _accumulated_cost(conversation)})


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
        _run(json.loads(line))
    except Exception as exc:  # noqa: BLE001 -- container boundary: report, don't crash silently
        _emit({"kind": "error", "text": f"{type(exc).__name__}: {exc}"})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
