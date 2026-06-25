"""Process-global sink for runtime tool-call outcome observations.

The provider boundary (``BaseCompletionProvider.complete`` / ``stream``)
emits tool-call outcomes here. The sink is installed once at boot
(``wire_tool_call_feedback``) with the
:class:`~synthorg.providers.tool_call_feedback.tracker.ToolCallFeedbackTracker`;
when no sink is installed (feature off / no persistence) :func:`emit_tool_call_outcome`
is a no-op. The provider boundary imports only this lightweight module,
so it never depends on the management / persistence layer.

The emit is ``await``-ed rather than fire-and-forget: the tracker keeps
an in-memory cache so a steady-state success is a pure no-op, and it
swallows its own errors, so awaiting it never blocks or breaks the hot
path. Awaiting (over scheduling a detached task) keeps ordering
deterministic and avoids loop-teardown task leaks under test. As a
second line of defence (the ``ToolCallSignalSink`` no-raise contract is
structural, not enforceable on every conformer), :func:`emit_tool_call_outcome`
also guards the ``record`` call so a misbehaving sink can never propagate
into -- or suppress the original exception of -- the provider call.
"""

from enum import Enum
from typing import Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_TOOL_CALL_FEEDBACK_RECORD_FAILED,
)

logger = get_logger(__name__)


class ToolCallOutcome(Enum):
    """Observed outcome of a tools-bearing completion request.

    ``FAILURE`` is a non-retryable provider rejection of a tools-bearing
    request (or a malformed tool-use engine response): evidence the model
    may be unable to call tools. ``SUCCESS`` is a response that actually
    contained tool calls: proof the model can call tools.
    """

    FAILURE = "failure"
    SUCCESS = "success"


@runtime_checkable
class ToolCallSignalSink(Protocol):
    """Receiver of tool-call outcome observations.

    Implementations MUST NOT raise: the sink is awaited inside the
    provider completion path, so an unhandled error would corrupt an
    otherwise-successful (or already-failing) provider call.
    """

    async def record(
        self,
        *,
        provider: str,
        model: str,
        outcome: ToolCallOutcome,
    ) -> None:
        """Record one ``(provider, model)`` tool-call outcome."""
        ...


_sink: ToolCallSignalSink | None = None


def install_tool_call_signal_sink(sink: ToolCallSignalSink) -> None:
    """Install the global tool-call signal sink (called once at boot).

    Once installed, :func:`emit_tool_call_outcome` routes every
    observation to ``sink`` (the tracker). Installed last in the wiring
    so a failed tracker build leaves no dangling sink.
    """
    global _sink  # noqa: PLW0603
    _sink = sink


def uninstall_tool_call_signal_sink() -> None:
    """Clear the global sink (shutdown / feature teardown)."""
    global _sink  # noqa: PLW0603
    _sink = None


def get_tool_call_signal_sink() -> ToolCallSignalSink | None:
    """Return the installed sink, or ``None`` when the feature is off.

    Returns:
        The tracker-backed sink installed at boot, or ``None`` when the
        runtime tool-call feedback loop is disabled (emit is then a
        no-op).
    """
    return _sink


async def emit_tool_call_outcome(
    *,
    provider: str,
    model: str,
    outcome: ToolCallOutcome,
) -> None:
    """Emit one tool-call outcome to the installed sink (no-op if unset).

    Args:
        provider: SynthOrg provider registry key.
        model: Model identifier within the provider.
        outcome: The observed :class:`ToolCallOutcome`.
    """
    sink = _sink
    if sink is None:
        return
    try:
        await sink.record(provider=provider, model=model, outcome=outcome)
    except Exception as exc:  # noqa: BLE001 -- the sink is awaited in the provider hot path; a raising sink must never break or mask the provider call
        reraise_critical(exc)
        logger.warning(
            PROVIDER_TOOL_CALL_FEEDBACK_RECORD_FAILED,
            provider=provider,
            model=model,
            outcome=outcome.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
