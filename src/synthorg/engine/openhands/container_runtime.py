# module-kind: adapter
"""In-sandbox OpenHands runtime: spawns the container, streams its events.

The real OpenHands run happens inside the ``docker/openhands`` container,
never in this process: the SDK and its native ``terminal`` / ``file_editor``
tools live only in the image. This adapter drives one run by streaming the
container's stdout through the injected :class:`DockerSandbox`:

1. Serialise the run spec to one JSON line.
2. Spawn the image entrypoint as a one-shot container whose egress is
   pinned (by the backend's sidecar allowlist) to exactly the gateway and
   credentialed-MCP hosts.
3. Feed the spec line to the container's stdin.
4. Parse each normalized JSON event line from stdout into an
   :class:`OpenHandsEvent` and forward it to the sink.

When the sink returns ``False`` (a budget / shutdown / cancellation
boundary tripped) the stream stops and the container is torn down. The
SDK never enters the main venv, so the litellm / dependency pin holds stay
out of the application environment entirely.
"""

import json
from collections.abc import AsyncGenerator
from typing import Final, Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.openhands.conversation import (
    EventSink,
    OpenHandsConversation,
    OpenHandsOutcome,
    OpenHandsRunSpec,
)
from synthorg.engine.openhands.errors import OpenHandsRuntimeError
from synthorg.engine.openhands.events import OpenHandsEvent, OpenHandsEventKind
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import EXECUTION_LOOP_ERROR
from synthorg.tools.sandbox.errors import SandboxError

logger = get_logger(__name__)


@runtime_checkable
class SandboxStreamer(Protocol):
    """The narrow sandbox surface the container runtime drives.

    Inverts the dependency on the concrete Docker backend: the runtime
    needs only a one-shot streaming spawn, so it depends on this protocol
    (which :class:`DockerSandbox` structurally satisfies) rather than the
    heavyweight backend, keeping the engine off the sandbox internals.
    """

    def stream_container_task(  # noqa: PLR0913 -- mirrors the backend surface
        self,
        *,
        command: NotBlankStr,
        args: tuple[str, ...],
        stdin_line: str,
        idle_timeout_seconds: float,
        category: str = "",
        project_id: NotBlankStr | None = None,
    ) -> AsyncGenerator[str]:
        """Run a one-shot container, yielding its stdout lines.

        Returns:
            An async generator over the container's stdout lines.
        """
        ...


_CONTAINER_PYTHON: Final[str] = "/opt/openhands/venv/bin/python"
_ENTRYPOINT: Final[str] = "/opt/openhands/run_task.py"

# The container-facing spec: the mount-selection ``project_id`` stays
# host-side and is deliberately excluded.
_CONTAINER_SPEC_FIELDS: Final[tuple[str, ...]] = (
    "task_prompt",
    "model",
    "gateway_base_url",
    "gateway_token",
    "mcp_base_url",
    "workspace_path",
    "conversation_id",
    "max_turns",
)

_KIND_BY_NAME: Final[dict[str, OpenHandsEventKind]] = {
    "action": OpenHandsEventKind.ACTION,
    "observation": OpenHandsEventKind.OBSERVATION,
    "message": OpenHandsEventKind.MESSAGE,
    "error": OpenHandsEventKind.ERROR,
    "finished": OpenHandsEventKind.FINISHED,
}

# Event kinds that correspond to an LLM turn, so may carry a cost delta.
_TURN_COST_KINDS: Final[frozenset[OpenHandsEventKind]] = frozenset(
    {OpenHandsEventKind.ACTION, OpenHandsEventKind.MESSAGE}
)


def _spec_line(spec: OpenHandsRunSpec) -> str:
    """Render the container-facing spec as one newline-terminated JSON line.

    Returns:
        The JSON spec line the container reads from stdin.
    """
    dumped = spec.model_dump(mode="json")
    payload = {key: dumped[key] for key in _CONTAINER_SPEC_FIELDS}
    return json.dumps(payload, separators=(",", ":")) + "\n"


def _parse_event(line: str, prev_cost: float) -> tuple[OpenHandsEvent | None, float]:
    """Parse one normalized JSON event line into an :class:`OpenHandsEvent`.

    The container reports a *running accumulated* cost per event; the
    per-turn cost forwarded to the loop is the delta since the previous
    event, so the loop's per-turn accumulation sums to the run total.

    Args:
        line: One JSON event line from the container's stdout.
        prev_cost: Accumulated cost reported by the previous event.

    Returns:
        ``(event, accumulated_cost)``. ``event`` is ``None`` for an
        unparseable or unknown line (skipped without terminating the run);
        ``accumulated_cost`` carries forward the running total.
    """
    try:
        payload = json.loads(line)
    except ValueError, TypeError:
        logger.warning(
            EXECUTION_LOOP_ERROR,
            loop_type="openhands",
            note="unparseable container event line",
        )
        return None, prev_cost
    if not isinstance(payload, dict):
        return None, prev_cost
    kind = _KIND_BY_NAME.get(str(payload.get("kind", "")))
    if kind is None:
        return None, prev_cost
    accumulated = _non_negative_float(payload.get("cost"))
    # Attribute the per-turn cost delta only on turn kinds (ACTION / MESSAGE);
    # the model rejects cost on the others, and the deltas already sum to the
    # run total. The accumulated total still advances for the next delta.
    delta = max(0.0, accumulated - prev_cost) if kind in _TURN_COST_KINDS else 0.0
    tool_name = payload.get("tool_name") if kind is OpenHandsEventKind.ACTION else None
    event = OpenHandsEvent(
        kind=kind,
        text=str(payload.get("text", "")),
        tool_name=tool_name if isinstance(tool_name, str) and tool_name else None,
        cost=delta,
    )
    return event, accumulated


def _non_negative_float(value: object) -> float:
    """Coerce a value to a non-negative float, defaulting to zero.

    Returns:
        The coerced value clamped to ``>= 0.0``.
    """
    if isinstance(value, int | float):
        return max(0.0, float(value))
    return 0.0


class _ContainerConversation:
    """Drives one OpenHands run by streaming its container's stdout.

    Each call to :meth:`run` spawns a fresh one-shot container, feeds the
    spec, forwards events to the sink, and tears the container down when
    the run ends, the sink stops it, or the coroutine is cancelled.
    """

    def __init__(
        self,
        *,
        sandbox: SandboxStreamer,
        spec: OpenHandsRunSpec,
        sink: EventSink,
        idle_timeout_seconds: float,
    ) -> None:
        self._sandbox = sandbox
        self._spec = spec
        self._sink = sink
        self._idle_timeout_seconds = idle_timeout_seconds

    async def run(self) -> OpenHandsOutcome:
        """Stream the container run, forwarding events until stop or finish.

        Returns:
            The terminal :class:`OpenHandsOutcome`.

        Raises:
            OpenHandsRuntimeError: When the container cannot run or its
                stream fails mid-flight.
        """
        stream = self._sandbox.stream_container_task(
            command=_CONTAINER_PYTHON,
            args=(_ENTRYPOINT,),
            stdin_line=_spec_line(self._spec),
            idle_timeout_seconds=self._idle_timeout_seconds,
            project_id=self._spec.project_id,
        )
        finished = False
        prev_cost = 0.0
        try:
            async for line in stream:
                event, prev_cost = _parse_event(line, prev_cost)
                if event is None:
                    continue
                if event.kind is OpenHandsEventKind.FINISHED:
                    finished = True
                if not await self._sink(event):
                    break
        except SandboxError as exc:
            logger.warning(
                EXECUTION_LOOP_ERROR,
                loop_type="openhands",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "OpenHands container run failed"
            raise OpenHandsRuntimeError(msg) from exc
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                EXECUTION_LOOP_ERROR,
                loop_type="openhands",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "OpenHands container stream failed"
            raise OpenHandsRuntimeError(msg) from exc
        finally:
            # aclose() drives the generator's finally (container + sidecar
            # teardown) on every exit path: natural end, sink stop, error,
            # or cancellation of the awaiting coroutine.
            await stream.aclose()
        return OpenHandsOutcome(finished=finished)


async def build_container_conversation(
    sandbox: SandboxStreamer,
    idle_timeout_seconds: float,
    spec: OpenHandsRunSpec,
    sink: EventSink,
) -> OpenHandsConversation:
    """Build a container-backed conversation for a run spec.

    Bound to the egress-pinned :class:`DockerSandbox` at wiring time (the
    first two arguments are fixed via ``functools.partial``), leaving the
    ``(spec, sink)`` signature the loop's conversation factory expects.

    Args:
        sandbox: The egress-pinned sandbox backend that spawns the run.
        idle_timeout_seconds: Max seconds to wait for the next event.
        spec: The run parameters (task, model, endpoints, token, resume id).
        sink: The async event sink the conversation forwards events to.

    Returns:
        A conversation satisfying :class:`OpenHandsConversation`.
    """
    return _ContainerConversation(
        sandbox=sandbox,
        spec=spec,
        sink=sink,
        idle_timeout_seconds=idle_timeout_seconds,
    )
