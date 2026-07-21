# module-kind: adapter
"""Prompt-caching breakpoint placement for the LiteLLM driver.

Prompt-caching-capable models reuse a cached prompt prefix when the request
marks stable blocks with ``cache_control: {"type": "ephemeral"}``. LiteLLM
forwards those markers through for caching-capable models. This module rewrites
the already-assembled ``acompletion`` kwargs to place breakpoints on the stable
prefix (the last system block, the tool definitions, and a rolling breakpoint
at the end of the conversation so far), which is what earns the cache hit on
the next turn.

Only caching-capable models are touched: a non-caching model receives the
request unchanged, so the ``cache_control`` markers never reach a backend that
would reject them.
"""

from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_PROMPT_CACHING_APPLIED,
    PROVIDER_PROMPT_CACHING_SKIPPED,
)
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.litellm_kwargs import _AcompletionKwargs

logger = get_logger(__name__)

# The prompt-caching provider family permits at most four cache breakpoints per
# request; the placement below uses at most three (system + tools + rolling
# tail), so the cap is a defensive ceiling rather than a live constraint.
_MAX_CACHE_BREAKPOINTS: Final[int] = 4

_EPHEMERAL: Final[dict[str, str]] = {"type": "ephemeral"}


def _mark_message_cache(message: dict[str, object]) -> bool:
    """Attach a cache_control breakpoint to a message's last content block.

    A plain-string ``content`` is rewritten into the single-text-block form so
    the breakpoint has a block to sit on; an existing block list gets the
    marker on its final block. A message with no markable content (e.g. an
    assistant tool-call-only message with ``content=None``) is left untouched.

    Returns:
        ``True`` when a breakpoint was placed, ``False`` otherwise.
    """
    content = message.get("content")
    if isinstance(content, str) and content:
        message["content"] = [
            {"type": "text", "text": content, "cache_control": dict(_EPHEMERAL)}
        ]
        return True
    if isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            last["cache_control"] = dict(_EPHEMERAL)
            return True
    return False


def _mark_tool_cache(tool: dict[str, object]) -> bool:
    """Attach a cache_control breakpoint to a tool definition.

    Returns:
        ``True`` (the tool dict always accepts the marker).
    """
    tool["cache_control"] = dict(_EPHEMERAL)
    return True


def _last_system_index(messages: list[dict[str, object]]) -> int | None:
    """Return the index of the last system message, or ``None``.

    Returns:
        The index of the final ``system``-role message, or ``None`` when the
        conversation has none.
    """
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "system":
            return index
    return None


def _last_markable_index(
    messages: list[dict[str, object]],
    *,
    skip: set[int],
) -> int | None:
    """Return the last message index with markable content, skipping *skip*.

    Returns:
        The index of the last message whose content can carry a breakpoint and
        is not already marked, or ``None`` when there is none.
    """
    for index in range(len(messages) - 1, -1, -1):
        if index in skip:
            continue
        content = messages[index].get("content")
        if (isinstance(content, str) and content) or (
            isinstance(content, list) and content
        ):
            return index
    return None


def apply_cache_control(
    kwargs: _AcompletionKwargs,
    *,
    capabilities: ModelCapabilities,
    provider_name: str,
    model_id: str,
) -> None:
    """Place cache_control breakpoints on the stable prefix, in place.

    No-op for a model that does not advertise prompt-caching support, so the
    ``cache_control`` markers never reach a backend that would reject them.
    Otherwise marks the last system block, the tool definitions, and a rolling
    breakpoint at the end of the conversation so far (bounded by
    :data:`_MAX_CACHE_BREAKPOINTS`).

    Args:
        kwargs: The assembled ``acompletion`` kwargs (mutated in place).
        capabilities: Resolved capabilities for the target model.
        provider_name: Owning provider name for the log event.
        model_id: Target model id for the log event.
    """
    if not capabilities.supports_prompt_caching:
        logger.debug(
            PROVIDER_PROMPT_CACHING_SKIPPED,
            provider=provider_name,
            model=model_id,
            reason="model_lacks_caching_support",
        )
        return

    messages = kwargs.get("messages") or []
    tools = kwargs.get("tools")
    marked: set[int] = set()
    breakpoints = 0

    system_index = _last_system_index(messages)
    if (
        system_index is not None
        and breakpoints < _MAX_CACHE_BREAKPOINTS
        and _mark_message_cache(messages[system_index])
    ):
        marked.add(system_index)
        breakpoints += 1

    if tools and breakpoints < _MAX_CACHE_BREAKPOINTS and _mark_tool_cache(tools[-1]):
        breakpoints += 1

    rolling_index = _last_markable_index(messages, skip=marked)
    if (
        rolling_index is not None
        and breakpoints < _MAX_CACHE_BREAKPOINTS
        and _mark_message_cache(messages[rolling_index])
    ):
        breakpoints += 1

    if breakpoints:
        logger.debug(
            PROVIDER_PROMPT_CACHING_APPLIED,
            provider=provider_name,
            model=model_id,
            breakpoints=breakpoints,
        )
