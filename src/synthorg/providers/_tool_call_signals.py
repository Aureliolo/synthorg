"""Tool-call outcome classification for the provider boundary.

Helpers extracted from :mod:`synthorg.providers.base` so the boundary
adapter stays under its module-size budget. Each function maps a
completion outcome on a tools-bearing request to a
:class:`~synthorg.providers.tool_call_feedback.sink.ToolCallOutcome` and
emits it through the global sink (a no-op when the feedback loop is off).
"""

from synthorg.core.completion_enums import FinishReason

from .errors import InvalidRequestError
from .models import CompletionResponse, ToolDefinition
from .tool_call_feedback.sink import ToolCallOutcome, emit_tool_call_outcome


async def emit_tool_call_failure_signal(
    exc: BaseException,
    provider_label: str,
    model: str,
    tools: list[ToolDefinition] | None,
) -> None:
    """Emit a tool-call FAILURE signal for a rejected tools-bearing request.

    Fires only when tools were requested AND the provider raised a
    non-retryable :class:`InvalidRequestError` -- the precise "provider
    rejected the tools-bearing request" signal. Transient/retryable errors
    (rate limit, timeout, connection, internal) and auth/quota/not-found
    never count, so a provider blip cannot downgrade a capable model.
    """
    if tools and isinstance(exc, InvalidRequestError):
        await emit_tool_call_outcome(
            provider=provider_label,
            model=model,
            outcome=ToolCallOutcome.FAILURE,
        )


async def emit_tool_call_response_signal(
    result: CompletionResponse,
    provider_label: str,
    model: str,
) -> None:
    """Emit a tool-call signal from a successful tools-bearing response.

    SUCCESS when the response actually carries tool calls (proof the model
    can call tools: clears any accumulated failure signal and re-enables a
    previously-downgraded model). FAILURE for a malformed ``TOOL_USE``
    response that carries no tool calls (the model signalled tool use but
    produced none, an unambiguous tool-calling malfunction). A plain text
    answer (the model legitimately did not need a tool this turn) emits
    nothing.
    """
    if result.tool_calls:
        await emit_tool_call_outcome(
            provider=provider_label,
            model=model,
            outcome=ToolCallOutcome.SUCCESS,
        )
    elif result.finish_reason is FinishReason.TOOL_USE:
        await emit_tool_call_outcome(
            provider=provider_label,
            model=model,
            outcome=ToolCallOutcome.FAILURE,
        )
