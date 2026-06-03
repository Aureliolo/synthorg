# module-kind: code
"""LLM plumbing for the substrate-backed grounding checker.

Pure, side-effect-free helpers split out of :mod:`substrate` so the
checker module stays focused on orchestration and the size budget holds.
Two structured tool calls drive the checker:

* ``extract_claims`` pulls the assertive factual claims out of the
  deliverable (the only attacker-controllable input, fenced via
  :func:`wrap_untrusted`).
* ``grounding_verdict`` judges one claim against the corpus chunks
  retrieved for it, returning a supported / uncertain / unsupported
  label plus the probability the claim is unsupported.

Both prompts are pinned to temperature ``0.0`` by the caller so the
deterministic simulation harness can replay golden runs. Outputs are
length-capped and control-char-stripped before they re-enter the
finding pipeline.
"""

import re
from typing import Final, Literal

from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    TAG_UNTRUSTED_ARTIFACT,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.knowledge.models import KnowledgeHit
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionResponse,
    ToolDefinition,
)

GroundingVerdictLabel = Literal["supported", "uncertain", "unsupported"]
"""Entailment outcomes; only ``unsupported`` produces an ungrounded claim."""

_VERDICT_LABELS: Final[frozenset[str]] = frozenset(
    {"supported", "uncertain", "unsupported"},
)

EXTRACT_CLAIMS_TOOL_NAME: Final[str] = "extract_claims"
GROUNDING_VERDICT_TOOL_NAME: Final[str] = "grounding_verdict"

MAX_CLAIMS: Final[int] = 32
"""Upper bound on claims evaluated per deliverable (bounds LLM-call cost)."""

MAX_DELIVERABLE_CHARS: Final[int] = 12000
"""Deliverable text is truncated to this before extraction (bounds prompt)."""

MAX_CLAIM_CHARS: Final[int] = 500
"""Per-claim length cap; keeps excerpts and rework briefs self-contained."""

MAX_CHUNK_CHARS: Final[int] = 1000
"""Per-evidence-chunk length cap in the entailment prompt."""

EXTRACTION_MAX_TOKENS: Final[int] = 1024
"""Output-token cap for the claim-extraction call."""

ENTAILMENT_MAX_TOKENS: Final[int] = 256
"""Output-token cap for the per-claim entailment call."""

LLM_TEMPERATURE: Final[float] = 0.0
"""Pinned temperature for both calls (harness determinism)."""

_CONFIDENCE_FLOOR: Final[float] = 0.0
_CONFIDENCE_CEILING: Final[float] = 1.0

_CONTROL_CHAR_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")
"""Strips control characters so a crafted claim cannot inject into logs."""

EXTRACT_CLAIMS_TOOL: Final[ToolDefinition] = ToolDefinition(
    name=EXTRACT_CLAIMS_TOOL_NAME,
    description=(
        "Return every assertive factual claim in the deliverable: specific "
        "numbers, percentages, named entities, dated events, and quantitative "
        "assertions stated as fact. Exclude questions, hedged statements, "
        "code, and generic prose. Do not invent claims."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Assertive factual claims, copied verbatim or lightly "
                    "normalised from the deliverable."
                ),
            },
        },
        "required": ["claims"],
        "additionalProperties": False,
    },
)

GROUNDING_VERDICT_TOOL: Final[ToolDefinition] = ToolDefinition(
    name=GROUNDING_VERDICT_TOOL_NAME,
    description=(
        "Judge whether the claim is supported by the retrieved corpus "
        "evidence. You MUST call this tool with your assessment."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["supported", "uncertain", "unsupported"],
                "description": (
                    "supported: evidence substantiates the claim. uncertain: "
                    "evidence is related but does not settle it. unsupported: "
                    "evidence is present but does not substantiate the claim."
                ),
            },
            "confidence": {
                "type": "number",
                "description": (
                    "Probability in [0,1] that the claim is UNSUPPORTED by the "
                    "evidence. Use a high value only when you are confident the "
                    "evidence fails to support the claim."
                ),
            },
            "reason": {
                "type": "string",
                "description": "Brief rationale (1-2 sentences).",
            },
        },
        "required": ["verdict", "confidence", "reason"],
        "additionalProperties": False,
    },
)

_EXTRACTION_SYSTEM_PROMPT: Final[str] = (
    "You are a grounding analyst for an AI agent's deliverable. Extract the "
    "assertive factual claims that should trace to a source: specific "
    "numbers, percentages, named entities, dated events, and quantitative "
    "assertions stated as fact. Ignore questions, hedged language, code "
    "blocks, and generic prose. You MUST call the extract_claims tool; do "
    "not respond with text.\n\n" + untrusted_content_directive((TAG_TASK_DATA,))
)

_ENTAILMENT_SYSTEM_PROMPT: Final[str] = (
    "You verify whether a single claim from a deliverable is supported by "
    "evidence snippets retrieved from the project's knowledge corpus. Bias "
    "toward 'supported' or 'uncertain': only return 'unsupported' when the "
    "evidence is present yet clearly fails to substantiate the claim. Never "
    "treat thin retrieval as proof of falsehood. The confidence you report "
    "is your probability that the claim is UNSUPPORTED. You MUST call the "
    "grounding_verdict tool; do not respond with text.\n\n"
    + untrusted_content_directive((TAG_TASK_DATA, TAG_UNTRUSTED_ARTIFACT))
)


def build_extraction_messages(deliverable_content: str) -> list[ChatMessage]:
    """Build the claim-extraction prompt for ``deliverable_content``.

    Returns:
        The system + user messages, with the deliverable fenced as
        untrusted task data and truncated to :data:`MAX_DELIVERABLE_CHARS`.
    """
    truncated = deliverable_content[:MAX_DELIVERABLE_CHARS]
    fenced = wrap_untrusted(TAG_TASK_DATA, truncated)
    return [
        ChatMessage(role=MessageRole.SYSTEM, content=_EXTRACTION_SYSTEM_PROMPT),
        ChatMessage(role=MessageRole.USER, content=fenced),
    ]


def build_entailment_messages(
    claim: str,
    hits: tuple[KnowledgeHit, ...],
) -> list[ChatMessage]:
    """Build the entailment prompt for one ``claim`` against ``hits``.

    Returns:
        The system + user messages, with the claim fenced as task data
        and the retrieved evidence fenced as an untrusted artifact.
    """
    fenced_claim = wrap_untrusted(TAG_TASK_DATA, claim[:MAX_CLAIM_CHARS])
    fenced_evidence = wrap_untrusted(TAG_UNTRUSTED_ARTIFACT, _format_evidence(hits))
    user_content = (
        f"<claim>\n{fenced_claim}\n</claim>\n<evidence>\n{fenced_evidence}\n</evidence>"
    )
    return [
        ChatMessage(role=MessageRole.SYSTEM, content=_ENTAILMENT_SYSTEM_PROMPT),
        ChatMessage(role=MessageRole.USER, content=user_content),
    ]


def _format_evidence(hits: tuple[KnowledgeHit, ...]) -> str:
    """Render retrieved chunks as a numbered, length-capped evidence block.

    Returns:
        The formatted evidence text; one entry per hit with its source id
        and relevance score.
    """
    lines: list[str] = []
    for index, hit in enumerate(hits, start=1):
        chunk = hit.chunk_text[:MAX_CHUNK_CHARS]
        lines.append(
            f"[{index}] (source={hit.citation.source_id}, "
            f"score={hit.relevance_score:.3f})\n{chunk}"
        )
    return "\n\n".join(lines)


def parse_extracted_claims(response: CompletionResponse) -> tuple[str, ...]:
    """Read the deduplicated, capped claim list from an extraction response.

    Returns:
        The extracted claims (stripped, control-char-cleaned, length- and
        count-capped); an empty tuple when the model returned none or did
        not call the tool.
    """
    arguments = _tool_arguments(response, EXTRACT_CLAIMS_TOOL_NAME)
    if arguments is None:
        return ()
    raw_claims = arguments.get("claims")
    if not isinstance(raw_claims, list):
        return ()
    seen: set[str] = set()
    claims: list[str] = []
    for raw in raw_claims:
        if not isinstance(raw, str):
            continue
        cleaned = _CONTROL_CHAR_RE.sub(" ", raw).strip()[:MAX_CLAIM_CHARS]
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        claims.append(cleaned)
        if len(claims) >= MAX_CLAIMS:
            break
    return tuple(claims)


def parse_grounding_verdict(
    response: CompletionResponse,
) -> tuple[GroundingVerdictLabel, float] | None:
    """Read the verdict label and clamped confidence from a verdict response.

    Returns:
        A ``(label, confidence)`` pair, or ``None`` when the model did not
        call the tool or returned an unparseable verdict / confidence.
    """
    arguments = _tool_arguments(response, GROUNDING_VERDICT_TOOL_NAME)
    if arguments is None:
        return None
    raw_verdict = arguments.get("verdict")
    if raw_verdict not in _VERDICT_LABELS:
        return None
    raw_confidence = arguments.get("confidence")
    if not isinstance(raw_confidence, (int, float)) or isinstance(raw_confidence, bool):
        return None
    confidence = max(
        _CONFIDENCE_FLOOR,
        min(_CONFIDENCE_CEILING, float(raw_confidence)),
    )
    label: GroundingVerdictLabel = raw_verdict  # type: ignore[assignment]
    return label, confidence


def _tool_arguments(
    response: CompletionResponse,
    tool_name: str,
) -> dict[str, object] | None:
    """Return the arguments of the first ``tool_name`` call, or ``None``.

    Returns:
        The tool-call arguments dict, or ``None`` when the model did not
        invoke that tool.
    """
    for call in response.tool_calls:
        if call.name == tool_name:
            return {**call.arguments}
    return None
