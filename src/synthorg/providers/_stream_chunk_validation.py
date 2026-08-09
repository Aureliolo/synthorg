# module-kind: code
"""Discriminated-union field validation for ``StreamChunk``.

Split out of :mod:`synthorg.providers.models` so that module stays within
its size budget. Pure: takes the chunk's field values and raises when the
event type's required / forbidden payload contract is violated. Typed
loosely (``object``) on the payload fields to avoid importing the model
types back from ``models`` (which would close an import cycle); only the
``StreamEventType`` discriminator needs its real type.
"""

from typing import assert_never

from synthorg.providers.enums import StreamEventType


def validate_stream_chunk_fields(
    *,
    event_type: StreamEventType,
    content: object,
    tool_call_delta: object,
    usage: object,
    error_message: object,
    finish_reason: object,
) -> None:
    """Enforce the per-event-type payload contract for a stream chunk.

    Each event type requires specific fields and rejects extraneous
    payload fields to maintain strict discriminated-union semantics.
    ``finish_reason`` is carried only on the terminal ``DONE`` event.

    Raises:
        ValueError: If required fields are missing, ``finish_reason`` is
            set on a non-terminal event, or extraneous fields are set.
    """
    payload: dict[str, object] = {
        "content": content,
        "tool_call_delta": tool_call_delta,
        "usage": usage,
        "error_message": error_message,
    }
    required: set[str] = set()
    match event_type:
        case StreamEventType.CONTENT_DELTA | StreamEventType.REASONING_DELTA:
            # Both carry text in ``content``; the discriminator says which of
            # the model's two channels it arrived on.
            required = {"content"}
        case StreamEventType.TOOL_CALL_DELTA:
            required = {"tool_call_delta"}
        case StreamEventType.USAGE:
            required = {"usage"}
        case StreamEventType.ERROR:
            required = {"error_message"}
        case StreamEventType.DONE:
            pass  # Terminal event, no required payload fields.
        case _ as unreachable:
            # A new event type without a payload contract is a type-check
            # error here, not a chunk that silently validates against nothing.
            assert_never(unreachable)

    for name in required:
        if payload[name] is None:
            msg = f"{event_type.value} event must include {name}"
            raise ValueError(msg)

    if finish_reason is not None and event_type is not StreamEventType.DONE:
        msg = f"{event_type.value} event must not include finish_reason"
        raise ValueError(msg)

    extraneous = sorted(
        name
        for name, value in payload.items()
        if name not in required and value is not None
    )
    if extraneous:
        fields = ", ".join(extraneous)
        msg = f"{event_type.value} event must not include {fields}"
        raise ValueError(msg)
