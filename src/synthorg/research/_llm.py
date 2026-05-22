"""Shared helpers for the research subsystem's LLM-backed strategies.

Centralises the provider call (pinned to deterministic sampling so a
recorded run replays identically) and the extraction of a JSON object from
a model response that may be wrapped in prose or a fenced code block.
"""

import re
from typing import TYPE_CHECKING, Final

from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig

if TYPE_CHECKING:
    from synthorg.providers.protocol import CompletionProvider

_DETERMINISTIC_TEMPERATURE: Final[float] = 0.0
"""Sampling temperature for every research LLM call: deterministic output
is a precondition for byte-identical cassette replay."""

_JSON_OBJECT_RE: Final[re.Pattern[str]] = re.compile(r"\{.*\}", re.DOTALL)
"""Matches the outermost JSON object in a response body, tolerating a
fenced code block or surrounding prose."""


def extract_json_object(content: str) -> str:
    """Return the outermost ``{...}`` JSON object found in *content*.

    Args:
        content: Raw model response text.

    Returns:
        The substring spanning the first ``{`` to the last ``}``.

    Raises:
        ValueError: If no JSON object delimiters are present.
    """
    match = _JSON_OBJECT_RE.search(content)
    if match is None:
        msg = "no JSON object found in model response"
        raise ValueError(msg)
    return match.group(0)


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
