"""Provider-layer enumerations."""

from enum import StrEnum


class AuthType(StrEnum):
    """Authentication type for an LLM provider."""

    API_KEY = "api_key"
    OAUTH = "oauth"
    CUSTOM_HEADER = "custom_header"
    SUBSCRIPTION = "subscription"
    NONE = "none"


class MessageRole(StrEnum):
    """Role of a message participant in a chat completion."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ImageMediaType(StrEnum):
    """MIME type of an image attached to a multimodal message."""

    PNG = "image/png"
    JPEG = "image/jpeg"
    WEBP = "image/webp"
    GIF = "image/gif"


class ImageDetail(StrEnum):
    """Vision-detail hint passed to multimodal models.

    Mirrors the chat-completion ``image_url.detail`` field. ``AUTO``
    lets the provider choose; ``LOW`` / ``HIGH`` trade tokens for
    fidelity.
    """

    AUTO = "auto"
    LOW = "low"
    HIGH = "high"


class StreamEventType(StrEnum):
    """Discriminator for streaming response chunks."""

    CONTENT_DELTA = "content_delta"
    TOOL_CALL_DELTA = "tool_call_delta"
    USAGE = "usage"
    ERROR = "error"
    DONE = "done"
