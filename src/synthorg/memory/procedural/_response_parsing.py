"""Shared LLM-response parsing for procedural-memory proposers.

Both the failure proposer (``proposer``) and the success proposer
(``success_proposer``) turn a raw LLM completion into a validated
``ProceduralMemoryProposal`` through the same guard sequence: empty
response, malformed JSON, schema-validation failure, and below-threshold
confidence, each logged under the shared procedural-memory events. The
failure proposer additionally binds ``task_id`` to every guard log; the
success proposer has no task context. This module is the single source of
that sequence so the two proposers cannot drift.

A JSON parse failure surfaces only the helper's fixed literal detail
category (``json_decode_error`` / ``json_wrong_top_level_type``), never
``str(exc)``: a ``JSONDecodeError`` carries ``exc.doc`` (the raw LLM
output), which could embed credentials from the executed task and leak
them into the log sink. That fixed category is safe to bind alongside
``task_id`` in the single malformed-JSON warning, so the actionable
warning carries both the task context and the failure kind.
"""

from collections.abc import Callable

from pydantic import ValidationError

from synthorg.core.json_parsing import extract_json_from_llm_response
from synthorg.memory.procedural.models import (
    ProceduralMemoryConfig,
    ProceduralMemoryProposal,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.procedural_memory import (
    PROCEDURAL_MEMORY_LOW_CONFIDENCE,
    PROCEDURAL_MEMORY_PROPOSED,
    PROCEDURAL_MEMORY_SKIPPED,
)

logger = get_logger(__name__)


def _extract_json(
    text: str,
    *,
    on_detail: Callable[[str], None] | None = None,
) -> dict[str, object] | None:
    """Extract a JSON object from LLM response text via the shared helper.

    A parse failure reports a fixed literal detail category to
    ``on_detail`` (never ``str(exc)``: a ``JSONDecodeError`` carries
    ``exc.doc``, the raw LLM output, which could embed task credentials)
    so the caller can surface it in one ``task_id``-bound line.

    Args:
        text: Raw LLM completion text.
        on_detail: Optional sink for the parse-failure detail category.

    Returns:
        The parsed object, or ``None`` when the text is not a valid JSON
        object (invalid JSON, or valid JSON whose top level is not an
        object).
    """
    return extract_json_from_llm_response(text, logger_callback=on_detail)


def _task_binding(task_id: str | None) -> dict[str, str]:
    """Return the ``task_id`` log binding, or empty when no task context."""
    return {"task_id": task_id} if task_id is not None else {}


def parse_proposal_response(
    content: str | None,
    config: ProceduralMemoryConfig,
    *,
    task_id: str | None = None,
) -> ProceduralMemoryProposal | None:
    """Parse and validate an LLM response into a procedural-memory proposal.

    Args:
        content: Raw LLM completion text, possibly ``None``.
        config: Proposer config supplying ``min_confidence``.
        task_id: Optional task identifier bound to each guard log line.

    Returns:
        The validated proposal, or ``None`` when the response is empty,
        unparseable, fails schema validation, or falls below the
        configured confidence threshold.
    """
    binding = _task_binding(task_id)

    if not content or not content.strip():
        logger.debug(PROCEDURAL_MEMORY_SKIPPED, reason="empty_response", **binding)
        return None

    parse_detail: list[str] = []
    data = _extract_json(content, on_detail=parse_detail.append)
    if data is None:
        logger.warning(
            PROCEDURAL_MEMORY_SKIPPED,
            reason="malformed_json",
            detail=parse_detail[0] if parse_detail else None,
            **binding,
        )
        return None

    try:
        proposal = ProceduralMemoryProposal.model_validate(data)
    except ValidationError as exc:
        logger.warning(
            PROCEDURAL_MEMORY_SKIPPED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            reason="validation_failed",
            **binding,
        )
        return None

    if proposal.confidence < config.min_confidence:
        logger.info(
            PROCEDURAL_MEMORY_LOW_CONFIDENCE,
            confidence=proposal.confidence,
            min_confidence=config.min_confidence,
            **binding,
        )
        return None

    logger.info(
        PROCEDURAL_MEMORY_PROPOSED,
        confidence=proposal.confidence,
        tags=proposal.tags,
        **binding,
    )
    return proposal
