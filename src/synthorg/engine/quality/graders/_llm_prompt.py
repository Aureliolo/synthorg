# module-kind: adapter
"""Prompt envelope construction for the LLM rubric grader.

Holds the grader tool schema, system prompt, payload limits, and the
pure block-rendering helpers that serialise the rubric, probes, and
artifact into the JSON grader envelope.
"""

from typing import Final, cast

from pydantic import JsonValue

from synthorg.engine.prompt_safety import (
    TAG_UNTRUSTED_ARTIFACT,
    untrusted_content_directive,
)
from synthorg.engine.quality.verification import (
    AtomicProbe,
    VerificationRubric,
    VerificationVerdict,
)
from synthorg.engine.workflow.handoff import HandoffArtifact

_GRADER_TOOL_NAME: Final[str] = "emit_rubric_verdict"
_GRADER_TOOL_DESCRIPTION: Final[str] = (
    "Emit a calibrated verdict for the artifact against the rubric.  "
    "Provide a grade in [0, 1] for every criterion by name, an overall "
    "verdict, a confidence in [0, 1], and short human-readable findings."
)
_GRADER_TOOL_SCHEMA: Final[dict[str, JsonValue]] = {
    "type": "object",
    "properties": {
        "per_criterion_grades": {
            "type": "object",
            "additionalProperties": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
        },
        "verdict": {
            "type": "string",
            "enum": [
                VerificationVerdict.PASS.value,
                VerificationVerdict.FAIL.value,
                VerificationVerdict.REFER.value,
            ],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["per_criterion_grades", "verdict", "confidence", "findings"],
    "additionalProperties": False,
}
_GRADER_SYSTEM_PROMPT: Final[str] = (
    "You are a calibrated verification evaluator.  Grade the artifact "
    "strictly against the rubric criteria using the calibration "
    "examples (when given) as anchor points.  Prefer REFER when the "
    "artifact is insufficient to decide.\n\n"
    + untrusted_content_directive((TAG_UNTRUSTED_ARTIFACT,))
)
_MAX_PAYLOAD_CHARS: Final[int] = 16_000
_DEFAULT_MAX_TOKENS: Final[int] = 2048
_GRADER_TOOL_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    cast("list[str]", _GRADER_TOOL_SCHEMA["required"]),
)


def render_rubric_block(rubric: VerificationRubric) -> dict[str, object]:
    """Serialize rubric criteria + calibration examples for the prompt.

    Returns:
        A JSON-serialisable dict carrying the rubric name, minimum
        confidence, the criteria list (name, description, weight,
        grade type), and the calibration examples.
    """
    calibration = [
        {
            "artifact_summary": ex.artifact_summary,
            "expected_verdict": ex.expected_verdict.value,
            "rationale": ex.rationale,
            "expected_grades": (
                dict(ex.expected_grades) if ex.expected_grades is not None else None
            ),
        }
        for ex in rubric.calibration_examples
    ]
    return {
        "name": rubric.name,
        "min_confidence": rubric.min_confidence,
        "criteria": [
            {
                "name": c.name,
                "description": c.description,
                "weight": c.weight,
                "grade_type": c.grade_type.value,
            }
            for c in rubric.criteria
        ],
        "calibration_examples": calibration,
    }


def render_probes_block(
    probes: tuple[AtomicProbe, ...],
) -> list[dict[str, object]]:
    """Serialize probes for the prompt.

    Returns:
        A list of probe dicts (``id`` / ``probe_text`` /
        ``source_criterion``) in input order.
    """
    return [
        {
            "id": p.id,
            "probe_text": p.probe_text,
            "source_criterion": p.source_criterion,
        }
        for p in probes
    ]


def render_artifact_block(
    artifact: HandoffArtifact,
    *,
    payload_text: str,
) -> dict[str, object]:
    """Serialize the artifact metadata + (possibly truncated) payload.

    Returns:
        A dict carrying the handoff endpoints, artifact refs, and the
        wrapped ``payload`` body (already passed through
        :func:`wrap_untrusted` by the caller).
    """
    return {
        "from_agent_id": artifact.from_agent_id,
        "to_agent_id": artifact.to_agent_id,
        "from_stage": artifact.from_stage,
        "to_stage": artifact.to_stage,
        "artifact_refs": list(artifact.artifact_refs),
        "payload": payload_text,
    }


def build_instructions(
    *,
    payload_truncated: bool,
    original_len: int,
) -> str:
    """Render the final instruction block, adding a truncation notice.

    Returns:
        The instruction text passed to the LLM, suffixed with an
        explicit truncation notice when ``payload_truncated`` is true so
        the model can route insufficient evidence to ``REFER``.
    """
    base = (
        "Call emit_rubric_verdict exactly once.  Provide a grade "
        "for every rubric criterion by name (use the criterion "
        "'name' field).  The overall verdict must be 'pass' only "
        "when the weighted evidence supports it; otherwise 'fail' "
        "or 'refer'.  Confidence reflects your certainty."
    )
    if not payload_truncated:
        return base
    return (
        base + f"  Note: the artifact payload was truncated from {original_len} "
        f"to {_MAX_PAYLOAD_CHARS} characters; if the visible payload is "
        "insufficient to decide, return 'refer' rather than guessing."
    )
