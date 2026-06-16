"""Shared helpers for the research subsystem's LLM-backed strategies.

Centralises the provider call (pinned to deterministic sampling so a
recorded run replays identically) and the extraction of a JSON object from
a model response that may be wrapped in prose or a fenced code block.
"""

import json
import re
from typing import Final

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker
from synthorg.core.types import NotBlankStr
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider

_DETERMINISTIC_TEMPERATURE: Final[float] = 0.0
"""Sampling temperature for every research LLM call: deterministic output
is a precondition for byte-identical cassette replay."""

_RESEARCH_AGENT_ID: Final[NotBlankStr] = NotBlankStr("system")
"""Attribution agent id for research spend (a SYSTEM-category activity)."""

_RESEARCH_FALLBACK_TASK_ID: Final[NotBlankStr] = NotBlankStr("system:research")
"""Task id used when a caller opens no cost scope (tracker absent); the
``cost_recording_scope`` short-circuits before this is read in that case."""

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


async def complete_text(  # noqa: PLR0913 -- cost-recording context is keyword-only DI
    provider: CompletionProvider,
    model: str,
    *,
    system: str,
    user: str,
    cost_tracker: CostTracker | None = None,
    task_id: NotBlankStr | None = None,
    project_id: NotBlankStr | None = None,
) -> tuple[str, float]:
    """Run a single deterministic completion and return its text and cost.

    When ``cost_tracker`` is provided the provider call runs inside a
    :func:`cost_recording_scope` so the spend is recorded as a
    SYSTEM-category :class:`CostRecord` (closing the research-spend blind
    spot in budgets / Prometheus / cost rollups). When it is ``None`` the
    scope is a no-op and behaviour is unchanged -- callers/tests without a
    tracker see no difference.

    Args:
        provider: The completion provider (cassette-wrappable).
        model: Model identifier to serve the request.
        system: System prompt (carries untrusted-content directives).
        user: User prompt (carries the wrapped untrusted payload).
        cost_tracker: Sink for the per-call cost record, or ``None`` to
            skip recording.
        task_id: Task attribution for the emitted record (research stage
            + brief id); ignored when ``cost_tracker`` is ``None``.
        project_id: Optional project attribution for the emitted record.

    Returns:
        A ``(content, cost)`` pair. ``content`` is the empty string
        when the provider returns no text.
    """
    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content=system),
        ChatMessage(role=MessageRole.USER, content=user),
    ]
    async with cost_recording_scope(
        cost_tracker=cost_tracker,
        agent_id=_RESEARCH_AGENT_ID,
        task_id=task_id or _RESEARCH_FALLBACK_TASK_ID,
        project_id=project_id,
        call_category=LLMCallCategory.SYSTEM,
    ):
        response = await provider.complete(
            messages,
            model,
            config=CompletionConfig(temperature=_DETERMINISTIC_TEMPERATURE),
        )
    return response.content or "", response.usage.cost
