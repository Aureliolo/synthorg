"""Tool-result fencing against prompt injection.

Split out of :mod:`loop_tool_execution` to keep that module under its
size cap: wrapping a tool's raw result against prompt injection is a
complete, self-contained concern, independent of dispatching tool
calls or applying their context side effects.
"""

import re
from typing import Final

from synthorg.engine.prompt_safety import (
    ALL_FENCE_TAGS,
    INJECTION_HEURISTICS,
    TAG_BRAIN_STATE,
    TAG_CODE_DIFF,
    TAG_COMPACTION_SUMMARY,
    TAG_CONFIG_VALUE,
    TAG_CRITERIA_JSON,
    TAG_DECIDER_NAME,
    TAG_DECISION_OPTION,
    TAG_KNOWLEDGE,
    TAG_LIVING_DOC,
    TAG_MEMORY_ENTRY,
    TAG_PEER_CONTRIBUTION,
    TAG_RESEARCH_SOURCE,
    TAG_TASK_DATA,
    TAG_TASK_FACT,
    TAG_TOOL_ARGUMENTS,
    TAG_TOOL_RESULT,
    TAG_UNTRUSTED_ARTIFACT,
    TAG_VERIFICATION_RUNS,
    wrap_untrusted,
)
from synthorg.observability import get_logger, scrub_secret_tokens
from synthorg.observability.events.tool import TOOL_INJECTION_PATTERN_DETECTED
from synthorg.providers.models import ToolResult

logger = get_logger(__name__)

# Common prompt-injection patterns that a tool might return in an
# attempt to take over the next LLM turn. Matches are flagged via
# ``TOOL_INJECTION_PATTERN_DETECTED`` for telemetry; the tool result
# is still wrapped in the fence, not rejected (rejection would
# break legitimate tools that echo user text in responses).
# Closing-tag look-alikes for every untrusted-content fence declared
# in ``synthorg.engine.prompt_safety``.  Listed explicitly rather than
# iterated from ``ALL_FENCE_TAGS`` so the pattern set is fixed at
# authoring time; the import-time guard below is what keeps the list
# complete.  Optional whitespace before ``>`` mirrors
# ``_escape_closing_tag`` so lenient variants (``</task-data >`` /
# ``</task-data\t>``) still trip.
_FENCE_TAGS: Final[tuple[str, ...]] = (
    TAG_TASK_DATA,
    TAG_TASK_FACT,
    TAG_COMPACTION_SUMMARY,
    TAG_TOOL_RESULT,
    TAG_TOOL_ARGUMENTS,
    TAG_UNTRUSTED_ARTIFACT,
    TAG_VERIFICATION_RUNS,
    TAG_CODE_DIFF,
    TAG_CONFIG_VALUE,
    TAG_CRITERIA_JSON,
    TAG_PEER_CONTRIBUTION,
    TAG_MEMORY_ENTRY,
    TAG_RESEARCH_SOURCE,
    TAG_LIVING_DOC,
    TAG_BRAIN_STATE,
    TAG_KNOWLEDGE,
    TAG_DECIDER_NAME,
    TAG_DECISION_OPTION,
)

# Import-time guard: every fence tag in the prompt-safety registry must
# appear in ``_FENCE_TAGS`` so its closing-tag breakout attempts are
# detected.  A new ``TAG_*`` constant added to ``prompt_safety`` without
# being listed here fails fast at import rather than silently dropping
# out of injection-detection coverage.
_MISSING_FENCE_TAGS = ALL_FENCE_TAGS - set(_FENCE_TAGS)
if _MISSING_FENCE_TAGS:
    _missing = ", ".join(sorted(_MISSING_FENCE_TAGS))
    _msg = (
        f"_FENCE_TAGS is missing prompt-safety registry tags: {_missing}. "
        f"Add them so closing-tag breakout detection stays complete."
    )
    raise ValueError(_msg)

_INJECTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # Shared "override the system prompt" heuristics (single source in
    # prompt_safety) plus per-tag closing-fence breakout patterns local to
    # tool-result wrapping.
    *INJECTION_HEURISTICS,
    *tuple(
        re.compile(rf"</{re.escape(tag)}\s*>", re.IGNORECASE) for tag in _FENCE_TAGS
    ),
)


def wrap_tool_result(result: ToolResult, *, scanned: str | None = None) -> ToolResult:
    """Return *result* with its ``content`` wrapped in ``<tool-result>``.

    Also emits ``TOOL_INJECTION_PATTERN_DETECTED`` when the content matches a
    known injection pattern (see :data:`_INJECTION_PATTERNS`). Detection is
    advisory; the wrap happens unconditionally so a malicious tool cannot
    escape the fence even if no pattern matches.

    Args:
        result: The tool result whose content is fenced.
        scanned: What the detection reads, when it is not the fenced content
            itself. A result abbreviated before it is fenced keeps its head
            and tail, and a payload placed in the elided middle would
            otherwise never reach the telemetry that records an attempt was
            made; the fence still covers exactly what the model sees.

    Returns:
        *result* with its ``content`` replaced by the fenced text.
    """
    raw = result.content
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(raw if scanned is None else scanned)
        if match is not None:
            # Scrub the telemetry sample before emitting -- if the
            # attacker embedded a credential inside the injection
            # payload, the raw ``sample=`` field would otherwise
            # leak it into logs.
            logger.warning(
                TOOL_INJECTION_PATTERN_DETECTED,
                tool_call_id=result.tool_call_id,
                pattern=pattern.pattern,
                sample=scrub_secret_tokens(match.string[: min(200, len(match.string))]),
            )
            break
    return result.model_copy(
        update={"content": wrap_untrusted(TAG_TOOL_RESULT, raw)},
    )


__all__ = ["wrap_tool_result"]
