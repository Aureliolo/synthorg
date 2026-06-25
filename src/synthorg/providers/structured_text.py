"""Shared helpers for LLM-backed structured-output strategies.

Centralises the deterministic provider call (pinned to temperature 0.0 so a
recorded run replays identically) and the extraction of a JSON object from a
model response that may be wrapped in prose or a fenced code block. The
research and knowledge generative-RAG synthesisers both build on these, so the
two cannot drift, and neither subsystem imports the other (which would create
an import cycle).
"""

import json
import re
from typing import Final

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.types import NotBlankStr
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider

DETERMINISTIC_TEMPERATURE: Final[float] = 0.0
"""Sampling temperature for every structured-output call: deterministic
output is a precondition for byte-identical cassette replay."""

SYSTEM_SPEND_AGENT_ID: Final[NotBlankStr] = NotBlankStr("system")
"""Attribution agent id for system-category subsystem spend (research,
knowledge synthesis); the spend is a SYSTEM activity, not an agent's."""

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
    agent_id: NotBlankStr = SYSTEM_SPEND_AGENT_ID,
    task_id: NotBlankStr,
    project_id: NotBlankStr | None = None,
    cost_tracker: CostTrackerProtocol | None = None,
    call_category: LLMCallCategory = LLMCallCategory.SYSTEM,
) -> tuple[str, float]:
    """Run a single deterministic completion and return its text and cost.

    When ``cost_tracker`` is provided the provider call runs inside a
    :func:`cost_recording_scope` so the spend is recorded as a
    :class:`CostRecord` under ``call_category`` (closing the subsystem-spend
    blind spot in budgets / Prometheus / cost rollups). When it is ``None``
    the scope is a no-op and behaviour is unchanged.

    Args:
        provider: The completion provider (cassette-wrappable).
        model: Model identifier to serve the request.
        system: System prompt (carries untrusted-content directives).
        user: User prompt (carries the wrapped untrusted payload).
        agent_id: Attribution agent id for the emitted record.
        task_id: Task attribution for the emitted record.
        project_id: Optional project attribution for the emitted record.
        cost_tracker: Sink for the per-call cost record, or ``None`` to
            skip recording.
        call_category: Budget call category for the emitted record.

    Returns:
        A ``(content, cost)`` pair. ``content`` is the empty string when the
        provider returns no text.
    """
    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content=system),
        ChatMessage(role=MessageRole.USER, content=user),
    ]
    async with cost_recording_scope(
        cost_tracker=cost_tracker,
        agent_id=agent_id,
        task_id=task_id,
        project_id=project_id,
        call_category=call_category,
    ):
        response = await provider.complete(
            messages,
            model,
            config=CompletionConfig(temperature=DETERMINISTIC_TEMPERATURE),
        )
    return response.content or "", response.usage.cost
