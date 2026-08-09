# module-kind: code
"""Translate between the OpenAI chat-completion wire shape and our models.

The gateway speaks the OpenAI ``/v1/chat/completions`` schema outward (so
an embedded harness's LiteLLM client just works) and the SynthOrg
provider models inward. Request models tolerate unknown OpenAI fields
(``extra="ignore"``) because clients send many we do not consume; every
consumed field maps onto a validated :class:`ChatMessage` /
:class:`ToolDefinition` / :class:`CompletionConfig`, so construction is
the typed boundary.
"""

import json
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from synthorg.core.completion_enums import FinishReason
from synthorg.core.domain_errors import ValidationError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.gateway import GATEWAY_DISPATCH_FAILED
from synthorg.providers.enums import (
    ImageDetail,
    ImageMediaType,
    MessageRole,
    StreamEventType,
)
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ImagePart,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    ToolResult,
)

logger = get_logger(__name__)

_DATA_URI_PREFIX: Final[str] = "data:"
_OBJECT_COMPLETION: Final[str] = "chat.completion"
_OBJECT_CHUNK: Final[str] = "chat.completion.chunk"

_FINISH_TO_OAI: Final[dict[FinishReason, str]] = {
    FinishReason.STOP: "stop",
    FinishReason.MAX_TOKENS: "length",
    FinishReason.TOOL_USE: "tool_calls",
    FinishReason.CONTENT_FILTER: "content_filter",
    FinishReason.ERROR: "stop",
}

_MEDIA_TYPES: Final[dict[str, ImageMediaType]] = {m.value: m for m in ImageMediaType}
_IMAGE_DETAILS: Final[dict[str, ImageDetail]] = {d.value: d for d in ImageDetail}


class _OAIFunctionCall(BaseModel):
    """The ``function`` block of an OpenAI assistant tool call."""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    arguments: str = "{}"


class _OAIToolCall(BaseModel):
    """An OpenAI assistant tool call request."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    function: _OAIFunctionCall = Field(default_factory=_OAIFunctionCall)


class _OAIMessage(BaseModel):
    """One OpenAI chat message before mapping to a :class:`ChatMessage`."""

    model_config = ConfigDict(extra="ignore")

    role: str
    content: str | list[dict[str, JsonValue]] | None = None
    tool_calls: list[_OAIToolCall] | None = None
    tool_call_id: str | None = None


class _OAIFunctionDef(BaseModel):
    """The ``function`` block of an OpenAI tool definition."""

    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class _OAITool(BaseModel):
    """An OpenAI tool definition."""

    model_config = ConfigDict(extra="ignore")

    function: _OAIFunctionDef


class GatewayChatRequest(BaseModel):
    """The subset of an OpenAI chat-completion request the gateway consumes.

    ``extra="ignore"`` tolerates the many OpenAI fields we do not act on
    (``user``, ``seed``, ``response_format`` and so on) without rejecting
    otherwise-valid requests.
    """

    model_config = ConfigDict(extra="ignore")

    model: NotBlankStr
    messages: list[_OAIMessage] = Field(default_factory=list)
    tools: list[_OAITool] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: str | list[str] | None = None
    timeout: float | None = None
    stream: bool = False


class ParsedChatRequest(BaseModel):
    """The gateway request mapped onto validated provider models."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    model: NotBlankStr
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()
    config: CompletionConfig | None = None
    stream: bool = False


def parse_chat_request(raw: dict[str, object]) -> ParsedChatRequest:
    """Map a raw OpenAI request dict onto validated provider models.

    Args:
        raw: The already-JSON-parsed request body.

    Returns:
        A :class:`ParsedChatRequest` with typed messages/tools/config.

    Raises:
        ValidationError: If the request or any message is malformed.
    """
    try:
        req = GatewayChatRequest.model_validate(raw)
    except ValueError as exc:
        msg = f"Malformed chat-completion request: {exc}"
        raise ValidationError(msg) from exc

    messages = tuple(_message_from_oai(m) for m in req.messages)
    tools = tuple(_tool_from_oai(t) for t in (req.tools or ()))
    return ParsedChatRequest(
        model=req.model,
        messages=messages,
        tools=tools,
        config=_config_from_oai(req),
        stream=req.stream,
    )


def _message_from_oai(message: _OAIMessage) -> ChatMessage:
    """Build a :class:`ChatMessage` from one OpenAI message.

    Returns:
        The mapped :class:`ChatMessage`.

    Raises:
        ValidationError: If the role is unknown or the shape violates a
            role constraint.
    """
    role = _role_from_oai(message.role)
    try:
        if role is MessageRole.TOOL:
            return _tool_message(message)
        if role is MessageRole.ASSISTANT:
            return _assistant_message(message)
        return _plain_message(role, message)
    except ValueError as exc:
        msg = f"Invalid {message.role} message: {exc}"
        raise ValidationError(msg) from exc


def _role_from_oai(role: str) -> MessageRole:
    """Map an OpenAI role string onto :class:`MessageRole`.

    Returns:
        The mapped :class:`MessageRole`.

    Raises:
        ValidationError: If the role is not one of the four known roles.
    """
    try:
        return MessageRole(role)
    except ValueError as exc:
        msg = f"Unknown message role: {role!r}"
        raise ValidationError(msg) from exc


def _tool_message(message: _OAIMessage) -> ChatMessage:
    """Build a tool-result message.

    Returns:
        A tool-role :class:`ChatMessage` carrying the result.

    Raises:
        ValueError: If ``tool_call_id`` is missing (ChatMessage rejects it).
    """
    if not message.tool_call_id:
        msg = "tool message requires tool_call_id"
        raise ValueError(msg)
    content, _ = _split_content(message.content)
    return ChatMessage(
        role=MessageRole.TOOL,
        tool_result=ToolResult(
            tool_call_id=message.tool_call_id, content=content or ""
        ),
    )


def _assistant_message(message: _OAIMessage) -> ChatMessage:
    """Build an assistant message, mapping any requested tool calls.

    Returns:
        An assistant-role :class:`ChatMessage`.
    """
    content, _ = _split_content(message.content)
    tool_calls = tuple(
        ToolCall(
            id=call.id or "call",
            name=call.function.name or "tool",
            arguments=_decode_arguments(call.function.arguments),
        )
        for call in (message.tool_calls or ())
    )
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content=content if content is not None or tool_calls else "",
        tool_calls=tool_calls,
    )


def _plain_message(role: MessageRole, message: _OAIMessage) -> ChatMessage:
    """Build a system or user message, extracting any image parts.

    Returns:
        A system- or user-role :class:`ChatMessage`.
    """
    content, images = _split_content(message.content)
    return ChatMessage(role=role, content=content, image_parts=images)


def _split_content(
    content: str | list[dict[str, JsonValue]] | None,
) -> tuple[str | None, tuple[ImagePart, ...]]:
    """Split OpenAI message content into text and image parts.

    Returns:
        A ``(text, image_parts)`` pair. ``text`` is ``None`` when the
        message has no textual content.

    Raises:
        ValueError: If an image part is not an inline ``data:`` URI.
    """
    if content is None:
        return None, ()
    if isinstance(content, str):
        return content, ()
    texts: list[str] = []
    images: list[ImagePart] = []
    for part in content:
        part_type = part.get("type")
        if part_type == "text":
            texts.append(str(part.get("text", "")))
        elif part_type == "image_url":
            images.append(_image_from_part(part))
    text = "".join(texts) if texts else None
    return text, tuple(images)


def _image_from_part(part: dict[str, JsonValue]) -> ImagePart:
    """Build an :class:`ImagePart` from an OpenAI ``image_url`` part.

    Returns:
        The mapped :class:`ImagePart`.

    Raises:
        ValueError: If the URL is not an inline ``data:`` URI or its media
            type is unsupported.
    """
    image_url = part.get("image_url")
    if not isinstance(image_url, dict):
        msg = "image_url part must carry an image_url object"
        raise ValueError(msg)  # noqa: TRY004 -- surfaced as ValidationError by caller
    url = str(image_url.get("url", ""))
    if not url.startswith(_DATA_URI_PREFIX):
        msg = "gateway accepts only inline data: image URIs"
        raise ValueError(msg)
    media_type, base64_data = _parse_data_uri(url)
    detail = _IMAGE_DETAILS.get(str(image_url.get("detail", "auto")), ImageDetail.AUTO)
    return ImagePart(media_type=media_type, base64_data=base64_data, detail=detail)


def _parse_data_uri(url: str) -> tuple[ImageMediaType, str]:
    """Parse ``data:<media>;base64,<data>`` into (media_type, data).

    Returns:
        The ``(media_type, base64_data)`` pair.

    Raises:
        ValueError: If the URI is not base64 or the media type is unknown.
    """
    header, _, data = url.partition(",")
    meta = header.removeprefix(_DATA_URI_PREFIX)
    media_raw, _, encoding = meta.partition(";")
    if encoding != "base64" or not data:
        msg = "data image URI must be base64-encoded"
        raise ValueError(msg)
    media_type = _MEDIA_TYPES.get(media_raw)
    if media_type is None:
        msg = f"unsupported image media type: {media_raw!r}"
        raise ValueError(msg)
    return media_type, data


def _decode_arguments(arguments: str) -> dict[str, JsonValue]:
    """Decode an OpenAI tool-call ``arguments`` JSON string.

    A non-object or unparseable value yields an empty dict rather than
    failing the whole request; the tool layer validates arguments again.

    Returns:
        The decoded arguments object, or ``{}`` when absent/malformed.
    """
    if not arguments:
        return {}
    try:
        decoded = json.loads(arguments)
    except ValueError, TypeError:
        logger.warning(
            GATEWAY_DISPATCH_FAILED,
            surface="gateway",
            reason="unparseable_tool_call_arguments",
        )
        return {}
    if not isinstance(decoded, dict):
        logger.warning(
            GATEWAY_DISPATCH_FAILED,
            surface="gateway",
            reason="non_object_tool_call_arguments",
        )
        return {}
    return decoded


def _tool_from_oai(tool: _OAITool) -> ToolDefinition:
    """Map an OpenAI tool definition onto a :class:`ToolDefinition`.

    Returns:
        The mapped :class:`ToolDefinition`.
    """
    return ToolDefinition(
        name=tool.function.name,
        description=tool.function.description,
        parameters_schema=tool.function.parameters,
    )


def _config_from_oai(req: GatewayChatRequest) -> CompletionConfig | None:
    """Build a :class:`CompletionConfig` from the request sampling fields.

    Returns:
        A config when any sampling field is set, else ``None`` so the
        provider fills its own defaults.
    """
    if (
        req.temperature is None
        and req.max_tokens is None
        and req.top_p is None
        and req.stop is None
        and req.timeout is None
    ):
        return None
    stop = (req.stop,) if isinstance(req.stop, str) else tuple(req.stop or ())
    return CompletionConfig(
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        top_p=req.top_p if req.top_p is not None else 1.0,
        stop_sequences=stop,
        timeout=req.timeout,
    )


def response_to_openai(
    response: CompletionResponse, *, response_id: str, created: int
) -> dict[str, object]:
    """Serialise a :class:`CompletionResponse` into an OpenAI response dict.

    Args:
        response: The provider completion result.
        response_id: A ``chatcmpl-*`` id for the response envelope.
        created: Unix epoch seconds the response was produced.

    Returns:
        An OpenAI ``chat.completion`` object.
    """
    message: dict[str, object] = {"role": "assistant"}
    if response.content is not None:
        message["content"] = response.content
    if response.tool_calls:
        message["tool_calls"] = [_tool_call_to_openai(c) for c in response.tool_calls]
    return {
        "id": response_id,
        "object": _OBJECT_COMPLETION,
        "created": created,
        "model": response.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": _FINISH_TO_OAI[response.finish_reason],
            }
        ],
        "usage": {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    }


def _tool_call_to_openai(call: ToolCall) -> dict[str, object]:
    """Serialise a :class:`ToolCall` into the OpenAI tool-call shape.

    Returns:
        The OpenAI ``{id, type, function}`` tool-call object.
    """
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments, separators=(",", ":")),
        },
    }


def stream_chunk_to_openai(
    chunk: StreamChunk, *, response_id: str, created: int, model: str
) -> dict[str, object] | None:
    """Serialise a :class:`StreamChunk` into an OpenAI streaming chunk.

    Args:
        chunk: The provider stream chunk.
        response_id: The ``chatcmpl-*`` id, stable across the stream.
        created: Unix epoch seconds, stable across the stream.
        model: The served model id.

    Returns:
        An OpenAI ``chat.completion.chunk`` object, or ``None`` for chunks
        that carry no client-visible delta (usage/done are handled by the
        gateway service's terminal SSE framing).
    """
    delta = _delta_for_chunk(chunk)
    if delta is None:
        return None
    finish = "tool_calls" if chunk.tool_call_delta is not None else None
    return {
        "id": response_id,
        "object": _OBJECT_CHUNK,
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _delta_for_chunk(chunk: StreamChunk) -> dict[str, object] | None:
    """Return the OpenAI ``delta`` for a stream chunk, or ``None``.

    Switched on the discriminator, not on which field happens to be set:
    a reasoning delta carries its text in ``content`` too, and reading the
    field alone would hand the model's own working to the harness as
    assistant output. It would then be replayed on the next turn as
    something the assistant said out loud, which is the one thing the
    reasoning channel is kept apart from ``content`` to prevent. It rides
    its own key, which a harness folds into the transcript only if it
    chooses to.
    """
    match chunk.event_type:
        case StreamEventType.CONTENT_DELTA if chunk.content is not None:
            return {"content": chunk.content}
        case StreamEventType.REASONING_DELTA if chunk.content is not None:
            return {"reasoning_content": chunk.content}
        case StreamEventType.TOOL_CALL_DELTA if chunk.tool_call_delta is not None:
            return {"tool_calls": [_tool_call_to_openai(chunk.tool_call_delta)]}
        case _:
            return None
