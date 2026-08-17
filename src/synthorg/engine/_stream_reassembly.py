# module-kind: code
"""Turning a chunk sequence back into the completion the loop consumes.

A streamed turn arrives as deltas across three channels plus a terminal event
carrying what no delta can say about the turn as a whole. This module holds
the accumulation and the rebuild, and nothing about driving the stream or
watching it for interruption, so the shape of a reassembled response is
decided in one place regardless of how the chunks were obtained.

Pure and synchronous throughout: everything here is a function of the chunks
already received, which is what lets the drain loop hand over a partially
filled accumulator after a mid-stream failure and still get an honest answer.
"""

from dataclasses import dataclass, field

from synthorg.core.completion_enums import FinishReason
from synthorg.providers.drivers.mappers import normalize_empty_finish
from synthorg.providers.enums import StreamEventType
from synthorg.providers.models import (
    ZERO_TOKEN_USAGE,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolCall,
)


@dataclass
class StreamAccumulator:
    """Mutable in-flight reassembly state for one streamed turn.

    Held by the caller (not just the drain loop) so a mid-stream exception
    still leaves whatever usage / content the stream surfaced before the
    failure visible for cost folding.
    """

    content_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = ZERO_TOKEN_USAGE
    finish_reason: FinishReason | None = None
    dropped_tool_calls: bool = False


def accumulate_chunk(chunk: StreamChunk, acc: StreamAccumulator) -> None:
    """Fold one stream chunk's payload into the reassembly accumulators."""
    if chunk.event_type is StreamEventType.CONTENT_DELTA and chunk.content:
        acc.content_parts.append(chunk.content)
    elif chunk.event_type is StreamEventType.REASONING_DELTA and chunk.content:
        acc.reasoning_parts.append(chunk.content)
    elif (
        chunk.event_type is StreamEventType.TOOL_CALL_DELTA
        and chunk.tool_call_delta is not None
    ):
        acc.tool_calls.append(chunk.tool_call_delta)


def reassemble_response(
    acc: StreamAccumulator,
    *,
    model_id: str,
    provider_name: str,
) -> CompletionResponse:
    """Reassemble streamed deltas into a ``CompletionResponse``.

    Recovers the finish reason from the terminal chunk when the driver
    surfaced one, else infers it (tool calls imply ``TOOL_USE``, otherwise
    ``STOP``). A completion empty on every channel is normalised to ``ERROR``
    through the same helper the non-streaming driver uses, so the built
    response is well-formed and the loop applies its own error handling.

    Through that helper rather than a second copy of the rule: a copy drifts,
    and the drift here turns a streamed empty turn into a bare ``ERROR`` whose
    log says only that the LLM returned an error, with no record that the turn
    was empty.

    Reasoning is kept as its own field rather than merged into *content*: it is
    the model's working, and replaying it back as assistant content changes
    what the model sees on the next turn.

    ``dropped_tool_calls`` is carried on the accumulator rather than inferred
    from an empty ``tool_calls``: the two turns it separates are the model
    sending a malformed call and the model sending none, they take opposite
    corrections, and from the chunks alone they are indistinguishable because
    a call that failed to assemble produced no chunk to count.

    Args:
        acc: The turn's accumulated deltas and terminal state.
        model_id: The model that served the turn.
        provider_name: The provider that served it, for the empty-turn log.

    Returns:
        The reassembled :class:`CompletionResponse`.
    """
    content = "".join(acc.content_parts) or None
    reasoning = "".join(acc.reasoning_parts) or None
    tool_calls = tuple(acc.tool_calls)
    finish = acc.finish_reason
    if finish is None:
        finish = FinishReason.TOOL_USE if tool_calls else FinishReason.STOP
    finish = normalize_empty_finish(
        content=content,
        reasoning=reasoning,
        tool_calls=tool_calls,
        finish=finish,
        provider=provider_name,
        model=model_id,
        # Raw calls DID arrive when one was dropped, which is what this
        # argument reports; reading it off the survivors would log an empty
        # turn as one the model never tried to call a tool on.
        had_raw_tool_calls=bool(tool_calls) or acc.dropped_tool_calls,
    )
    return CompletionResponse(
        content=content,
        reasoning=reasoning,
        tool_calls=tool_calls,
        dropped_tool_calls=acc.dropped_tool_calls,
        finish_reason=finish,
        usage=acc.usage,
        model=model_id,
    )


__all__ = ["StreamAccumulator", "accumulate_chunk", "reassemble_response"]
