"""Shared helpers for the research subsystem's LLM-backed strategies.

Centralises the provider call (pinned to deterministic sampling so a
recorded run replays identically) and the extraction of a JSON object from
a model response that may be wrapped in prose or a fenced code block.
"""

import json
import re
from typing import TYPE_CHECKING, Final

from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig

if TYPE_CHECKING:
    from synthorg.providers.protocol import CompletionProvider

_DETERMINISTIC_TEMPERATURE: Final[float] = 0.0
"""Sampling temperature for every research LLM call: deterministic output
is a precondition for byte-identical cassette replay."""

_JSON_OBJECT_START_RE: Final[re.Pattern[str]] = re.compile(r"\{", re.DOTALL)
"""Locates candidate JSON-object starts in a response body."""


def extract_json_object(content: str) -> str:
    """Return the first balanced ``{...}`` JSON object found in *content*.

    Scans each ``{`` and asks the JSON decoder to consume a single object
    from that point, so prose or fenced code with stray braces around the
    payload cannot extend the match to an invalid span.

    Args:
        content: Raw model response text.

    Returns:
        The substring spanning the first decodable JSON object.

    Raises:
        ValueError: If no JSON object is present.
    """
    decoder = json.JSONDecoder()
    for match in _JSON_OBJECT_START_RE.finditer(content):
        start = match.start()
        try:
            parsed, end = decoder.raw_decode(content[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return content[start : start + end]
    msg = "no JSON object found in model response"
    raise ValueError(msg)


async def complete_text(
    provider: CompletionProvider,
    model: str,
    *,
    system: str,
    user: str,
) -> tuple[str, float]:
    """Run a single deterministic completion and return its text and cost.

    Args:
        provider: The completion provider (cassette-wrappable).
        model: Model identifier to serve the request.
        system: System prompt (carries untrusted-content directives).
        user: User prompt (carries the wrapped untrusted payload).

    Returns:
        A ``(content, cost)`` pair. ``content`` is the empty string
        when the provider returns no text.
    """
    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content=system),
        ChatMessage(role=MessageRole.USER, content=user),
    ]
    response = await provider.complete(
        messages,
        model,
        config=CompletionConfig(temperature=_DETERMINISTIC_TEMPERATURE),
    )
    return response.content or "", response.usage.cost
