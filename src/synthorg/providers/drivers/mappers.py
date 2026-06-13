"""Pure mapping functions between domain models and LLM API dict formats.

These mappers convert between ``synthorg.providers.models`` and the
standard chat-completion dict format that LiteLLM (and most providers)
consume.  Reusable by future native SDK drivers.
"""

import copy
import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from pydantic import JsonValue

from synthorg.core.completion_enums import FinishReason
from synthorg.core.normalization import compare_ci
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_FINISH_REASON_UNKNOWN,
    PROVIDER_RETRY_AFTER_PARSE_FAILED,
    PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
    PROVIDER_TOOL_CALL_INCOMPLETE,
    PROVIDER_TOOL_CALL_MISSING_FUNCTION,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, ToolCall, ToolDefinition

logger = get_logger(__name__)


def _parse_retry_after_seconds(
    raw: object,
    now: datetime | None = None,
) -> float | None:
    """Parse a ``Retry-After`` value into a delay in seconds.

    Accepts both RFC 9110 §10.2.3 forms: a delta-seconds number
    (``"120"``) and an HTTP-date (``"Wed, 21 Oct 2026 07:28:00 GMT"``).
    An HTTP-date is converted to the delay from ``now``; a past date
    yields a negative delay, which the caller's finite/non-negative
    guard rejects. Returns ``None`` when the value matches neither form.

    Args:
        raw: The raw header value (delta-seconds string or HTTP-date).
        now: Reference instant for the HTTP-date delta; defaults to the
            current UTC time. Injectable so tests are deterministic
            without depending on wall-clock timing.

    Returns:
        The parsed seconds (possibly negative for a past date), or
        ``None`` when unparseable.
    """
    try:
        return float(raw)  # type: ignore[arg-type]
    except ValueError, TypeError, OverflowError:
        pass
    if not isinstance(raw, str):
        return None
    try:
        retry_dt = parsedate_to_datetime(raw)
    except ValueError, TypeError:
        return None
    if retry_dt.tzinfo is None:
        retry_dt = retry_dt.replace(tzinfo=UTC)
    current = now if now is not None else datetime.now(UTC)
    return (retry_dt - current).total_seconds()


def extract_retry_after(exc: Exception) -> float | None:
    """Extract ``retry-after`` seconds from exception headers.

    Args:
        exc: The provider exception, which may carry HTTP ``headers``.

    Returns:
        The ``retry-after`` seconds as a ``float``, or ``None`` when
        the header is absent, not a mapping, or unparseable.
    """
    headers = getattr(exc, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    # Case-insensitive lookup per HTTP semantics. The value is untyped
    # (a malformed header may carry a list / number), so keep it as
    # ``object`` and let ``_parse_retry_after_seconds`` reject non-strings.
    raw: object = None
    for key, value in headers.items():
        if isinstance(key, str) and compare_ci(key, "retry-after"):
            raw = value
            break
    if raw is None:
        return None
    parsed = _parse_retry_after_seconds(raw)
    # ``float`` accepts ``"inf"`` / ``"nan"`` and signed values, and a
    # past HTTP-date yields a negative delay; a retry-after must be a
    # finite, non-negative number of seconds. A past date is a benign
    # "retry now" the backoff handler treats as no hint.
    if parsed is None or not math.isfinite(parsed) or parsed < 0:
        logger.debug(
            PROVIDER_RETRY_AFTER_PARSE_FAILED,
            raw_value=repr(raw),
        )
        return None
    return parsed


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
        if not (
            isinstance(call_id, str) and isinstance(name, str) and call_id and name
        ):
            logger.warning(
                PROVIDER_TOOL_CALL_INCOMPLETE,
                tool_id=call_id,
                tool_name=name,
            )
            continue
        raw_args = _get(func, "arguments", "{}")
        arguments = _parse_arguments(raw_args, tool_id=call_id, tool_name=name)
        # Drop the tool call when arguments cannot be parsed rather than
        # emitting one with silently-emptied arguments: the streaming
        # accumulator path drops on the same failures, so a real tool never
        # runs with the wrong (empty) arguments in either path.
        if arguments is None:
            continue
        calls.append(ToolCall(id=call_id, name=name, arguments=arguments))

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


def _parse_arguments(
    raw: object,
    *,
    tool_id: str,
    tool_name: str,
) -> dict[str, JsonValue] | None:
    """Parse tool call arguments from string or dict form.

    Expected inputs are ``str`` (JSON) or ``dict``, but any type is
    accepted so callers need not pre-validate LLM response shapes.

    Args:
        raw: JSON string, pre-parsed dict, or other value.
        tool_id: The owning tool call id, for failure-log correlation.
        tool_name: The owning tool name, for failure-log correlation.

    Returns:
        The parsed arguments dict, or ``None`` when the arguments cannot
        be parsed, are not a JSON object, or are not finite,
        JSON-serialisable values.  The caller drops the tool call on
        ``None`` rather than emitting one with silently-emptied arguments.
    """
    if isinstance(raw, dict):
        candidate: dict[str, JsonValue] = dict(raw)
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            logger.warning(
                PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
                tool_id=tool_id,
                tool_name=tool_name,
                args_length=len(raw),
            )
            return None
        if not isinstance(parsed, dict):
            logger.warning(
                PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
                tool_id=tool_id,
                tool_name=tool_name,
                args_length=len(raw),
                parsed_type=type(parsed).__name__,
            )
            return None
        candidate = dict(parsed)
    else:
        logger.warning(
            PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
            tool_id=tool_id,
            tool_name=tool_name,
            raw_type=type(raw).__name__,
            reason="unexpected_arguments_type",
        )
        return None
    # ``ToolCall.arguments`` forbids non-finite floats (allow_inf_nan=False)
    # and must hold JSON-serialisable values.  ``json.loads`` accepts the
    # ``NaN`` / ``Infinity`` literals by default, so gate the result here:
    # arguments that will not round-trip drop the tool call instead of
    # raising a ValidationError when the ``ToolCall`` is constructed.
    try:
        json.dumps(candidate, allow_nan=False)
    except ValueError, TypeError:
        logger.warning(
            PROVIDER_TOOL_CALL_ARGUMENTS_PARSE_FAILED,
            tool_id=tool_id,
            tool_name=tool_name,
            reason="non_finite_or_unserialisable",
        )
        return None
    return candidate
