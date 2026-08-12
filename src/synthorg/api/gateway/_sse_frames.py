# module-kind: code
"""The SSE frames the streaming gateway emits, and how they are framed.

Everything the client reads off a stream is built here: the ordinary chunks
carrying a delta, the terminal usage chunk, and the two chunks that end a
stream abnormally. Kept together because they share one contract, that a
consumer reading to ``[DONE]`` can tell a normal stop from an error and from
a budget kill, and that contract is easier to hold in one file than to
rediscover across the dispatch code.
"""

import json
from typing import Final

from synthorg.providers.models import StreamChunk, TokenUsage

_OBJECT_CHUNK: Final[str] = "chat.completion.chunk"

#: Terminates every stream, including one cut short by an error or a kill.
SSE_DONE: Final[str] = "data: [DONE]\n\n"


def tool_call_index(chunk: StreamChunk, indices: dict[str, int]) -> int | None:
    """Resolve this chunk's position among the response's tool calls.

    Assigned in first-seen order and remembered by call id, so every fragment
    of one call carries the same position and two calls never share one.

    Args:
        chunk: The provider stream chunk.
        indices: Call id to position, extended here as new calls appear.

    Returns:
        The position, or ``None`` when the chunk carries no tool call.
    """
    if chunk.tool_call_delta is None:
        return None
    call_id = chunk.tool_call_delta.id
    if call_id not in indices:
        indices[call_id] = len(indices)
    return indices[call_id]


def sse(body: dict[str, object]) -> str:
    """Frame *body* as an SSE ``data:`` event.

    Returns:
        The SSE ``data:`` frame text.
    """
    return f"data: {json.dumps(body, separators=(',', ':'))}\n\n"


def usage_chunk(
    usage: TokenUsage, *, response_id: str, created: int, model: str
) -> dict[str, object]:
    """Build the terminal usage chunk of a stream.

    Sent only when the client set ``stream_options.include_usage``, which is
    the OpenAI contract: a client that did not ask expects every chunk to
    carry a choice, so an unrequested one would break its parser.

    Args:
        usage: The token counts the provider reported for the request.
        response_id: The ``chatcmpl-*`` id, stable across the stream.
        created: Unix epoch seconds, stable across the stream.
        model: The served model id.

    Returns:
        An OpenAI ``chat.completion.chunk`` reporting token counts only.
    """
    return {
        "id": response_id,
        "object": _OBJECT_CHUNK,
        "created": created,
        "model": model,
        # Empty by contract: this chunk reports the request's totals rather
        # than extending the message.
        "choices": [],
        "usage": {
            "prompt_tokens": usage.input_tokens,
            "completion_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        },
    }


def usage_frame(
    usage: TokenUsage, *, response_id: str, created: int, model: str
) -> str:
    """Frame the terminal usage chunk a client asked to be sent.

    Returns:
        The SSE frame reporting the request's token counts.
    """
    return sse(
        usage_chunk(usage, response_id=response_id, created=created, model=model)
    )


def error_chunk(
    chunk: StreamChunk, response_id: str, created: int, model: str
) -> dict[str, object]:
    """Build an OpenAI-style error chunk from a provider error chunk.

    Returns:
        An OpenAI ``chat.completion.chunk`` carrying an ``error`` object.
    """
    return {
        "id": response_id,
        "object": _OBJECT_CHUNK,
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "error": {
            "message": chunk.error_message or "stream error",
            "type": "gateway_stream_error",
        },
    }


def budget_kill_chunk(response_id: str, created: int, model: str) -> dict[str, object]:
    """Build the terminal chunk emitted when a run's cost ceiling is hit.

    The buffered path surfaces budget exhaustion as a hard ``402`` error; the
    streaming path must give the consuming harness an equally unambiguous
    signal rather than a truncated-but-otherwise-normal stream, so it carries
    both a ``finish_reason="length"`` and an explicit ``error`` object.

    Returns:
        An OpenAI ``chat.completion.chunk`` marking a budget-exhaustion kill.
    """
    return {
        "id": response_id,
        "object": _OBJECT_CHUNK,
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "length"}],
        "error": {
            "message": "run cost ceiling exceeded; stream terminated",
            "type": "gateway_budget_exhausted",
        },
    }


__all__ = [
    "SSE_DONE",
    "budget_kill_chunk",
    "error_chunk",
    "sse",
    "tool_call_index",
    "usage_chunk",
    "usage_frame",
]
