"""Pure mapping functions between domain models and LLM API dict formats.

These mappers convert between ``synthorg.providers.models`` and the
standard chat-completion dict format that LiteLLM (and most providers)
consume.  Reusable by future native SDK drivers.
"""

import copy
import json

from pydantic import JsonValue

from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_FINISH_REASON_UNKNOWN,
    PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
    PROVIDER_TOOL_CALL_INCOMPLETE,
    PROVIDER_TOOL_CALL_MISSING_FUNCTION,
)
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import ChatMessage, ToolCall, ToolDefinition

logger = get_logger(__name__)


def messages_to_dicts(messages: list[ChatMessage]) -> list[dict[str, object]]:
    """Convert a list of ``ChatMessage`` to chat-completion message dicts.

    Args:
        messages: Domain message objects.

    Returns:
        List of dicts ready for the ``messages`` parameter of
        ``litellm.acompletion``.
    """
    return [_message_to_dict(m) for m in messages]


def _message_to_dict(message: ChatMessage) -> dict[str, object]:
    """Convert a single ``ChatMessage`` to a dict.

    Returns:
        A chat-completion message dict for the given ``ChatMessage``.
    """
    result: dict[str, object] = {"role": message.role.value}

    match message.role:
        case MessageRole.TOOL:
            tr = message.tool_result
            result["content"] = tr.content if tr else ""
            result["tool_call_id"] = tr.tool_call_id if tr else ""
        case MessageRole.ASSISTANT:
            if message.content is not None:
                result["content"] = message.content
            if message.tool_calls:
                result["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in message.tool_calls
                ]
        case _:
            if message.image_parts:
                result["content"] = _multimodal_content(message)
            else:
                result["content"] = message.content or ""

    return result


def _multimodal_content(message: ChatMessage) -> list[dict[str, object]]:
    """Build the litellm multimodal content list for a user message.

    Emits a leading ``text`` part only when ``content`` is non-empty,
    followed by one ``image_url`` part per attached image (order
    preserved).

    Returns:
        A list of content-part dicts (text and image_url) for the
        message's multimodal payload.
    """
    parts: list[dict[str, object]] = []
    if message.content:
        parts.append({"type": "text", "text": message.content})
    parts.extend(
        {
            "type": "image_url",
            "image_url": {
                "url": image.data_uri,
                "detail": image.detail.value,
            },
        }
        for image in message.image_parts
    )
    return parts


def tools_to_dicts(tools: list[ToolDefinition]) -> list[dict[str, object]]:
    """Convert a list of ``ToolDefinition`` to chat-completion tool dicts.

    Args:
        tools: Domain tool definitions.

    Returns:
        List of dicts ready for the ``tools`` parameter of
        ``litellm.acompletion``.
    """
    return [_tool_to_dict(t) for t in tools]


def _tool_to_dict(tool: ToolDefinition) -> dict[str, object]:
    """Convert a single ``ToolDefinition`` to a chat-completion tool dict.

    Returns:
        A chat-completion tool dict for the given ``ToolDefinition``.
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": copy.deepcopy(tool.parameters_schema),
        },
    }


# Different providers use varying finish-reason strings natively.
# LiteLLM normalises most responses but some pass through raw.
_FINISH_REASON_MAP: dict[str | None, FinishReason] = {
    "stop": FinishReason.STOP,
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "length": FinishReason.MAX_TOKENS,
    "max_tokens": FinishReason.MAX_TOKENS,
    "tool_calls": FinishReason.TOOL_USE,
    "function_call": FinishReason.TOOL_USE,
    "tool_use": FinishReason.TOOL_USE,
    "content_filter": FinishReason.CONTENT_FILTER,
}


def map_finish_reason(reason: str | None) -> FinishReason:
    """Map a provider finish reason string to ``FinishReason``.

    Args:
        reason: Raw finish reason from the provider (e.g. ``"stop"``),
            or ``None`` if no finish reason was provided (common in
            streaming intermediate chunks).

    Returns:
        The corresponding ``FinishReason`` enum member.  Unmapped values
        (including ``None``) default to ``FinishReason.ERROR``.
    """
    result = _FINISH_REASON_MAP.get(reason)
    if result is None:
        if reason is not None:
            logger.warning(
                PROVIDER_FINISH_REASON_UNKNOWN,
                reason=reason,
            )
        return FinishReason.ERROR
    return result


def extract_tool_calls(raw: list[object] | None) -> tuple[ToolCall, ...]:
    """Extract ``ToolCall`` objects from raw chat-completion tool call dicts.

    Handles both parsed dicts and objects with attribute access (as
    returned by LiteLLM response objects).

    Args:
        raw: List of tool call dicts/objects from the provider response,
            or ``None`` if no tool calls.

    Returns:
        Tuple of ``ToolCall`` domain objects.
    """
    if not raw:
        return ()

    calls: list[ToolCall] = []
    for item in raw:
        call_id = _get(item, "id", "")
        func = _get(item, "function", None)
        if func is None:
            logger.warning(
                PROVIDER_TOOL_CALL_MISSING_FUNCTION,
                item_type=type(item).__name__,
            )
            continue
        name = _get(func, "name", "")
        raw_args = _get(func, "arguments", "{}")
        arguments = _parse_arguments(raw_args)
        if isinstance(call_id, str) and isinstance(name, str) and call_id and name:
            calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
        else:
            logger.warning(
                PROVIDER_TOOL_CALL_INCOMPLETE,
                tool_id=call_id,
                tool_name=name,
            )

    return tuple(calls)


def _get(obj: object, key: str, default: object) -> object:
    """Get a value from a dict or object attribute.

    Returns:
        The value for *key* from a dict or object attribute, or *default*
        when absent.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _parse_arguments(raw: object) -> dict[str, JsonValue]:
    """Parse tool call arguments from string or dict form.

    Expected inputs are ``str`` (JSON) or ``dict``, but any type is
    accepted so callers need not pre-validate LLM response shapes.

    Args:
        raw: JSON string, pre-parsed dict, or other value.

    Returns:
        Parsed arguments dict.  Returns empty dict on parse failure or
        when the arguments are not finite, JSON-serialisable values.
    """
    if isinstance(raw, dict):
        candidate: dict[str, JsonValue] = dict(raw)
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError, ValueError:
            logger.warning(
                PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
                args_length=len(raw),
            )
            return {}
        if not isinstance(parsed, dict):
            logger.warning(
                PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
                args_length=len(raw),
                parsed_type=type(parsed).__name__,
            )
            return {}
        candidate = dict(parsed)
    else:
        return {}
    # ``ToolCall.arguments`` forbids non-finite floats (allow_inf_nan=False)
    # and must hold JSON-serialisable values.  ``json.loads`` accepts the
    # ``NaN`` / ``Infinity`` literals by default, so gate the result here:
    # arguments that will not round-trip degrade to ``{}`` instead of
    # raising a ValidationError when the ``ToolCall`` is constructed.
    try:
        json.dumps(candidate, allow_nan=False)
    except ValueError, TypeError:
        logger.warning(
            PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
            reason="non_finite_or_unserialisable",
        )
        return {}
    return candidate
