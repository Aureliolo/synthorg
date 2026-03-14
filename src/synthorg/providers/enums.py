"""Provider-layer enumerations."""

from enum import StrEnum


class MessageRole(StrEnum):
    """Role of a message participant in a chat completion."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    """Reason the model stopped generating tokens."""

    STOP = "stop"
    MAX_TOKENS = "max_tokens"
    TOOL_USE = "tool_use"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"


class StreamEventType(StrEnum):
    """Discriminator for streaming response chunks."""

    CONTENT_DELTA = "content_delta"
    TOOL_CALL_DELTA = "tool_call_delta"
    USAGE = "usage"
    ERROR = "error"
    DONE = "done"
