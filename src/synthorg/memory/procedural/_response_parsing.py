"""Shared LLM-response parsing for procedural-memory proposers.

Both the failure proposer (``proposer``) and the success proposer
(``success_proposer``) turn a raw LLM completion into a validated
``ProceduralMemoryProposal`` through the same guard sequence: empty
response, malformed JSON, schema-validation failure, and below-threshold
confidence, each logged under the shared procedural-memory events. The
failure proposer additionally binds ``task_id`` to every guard log; the
success proposer has no task context. This module is the single source of
that sequence so the two proposers cannot drift.

The JSON extraction never binds ``task_id``: a parse failure logs only the
helper's fixed literal detail, never ``str(exc)``, because a
``JSONDecodeError`` carries ``exc.doc`` (the raw LLM output), which could
embed credentials from the executed task and leak them into the log sink.
"""

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


def _extract_json(text: str) -> dict[str, object] | None:
    """Extract a JSON object from LLM response text via the shared helper.

    Returns:
        The parsed object, or ``None`` when the text is not valid JSON.
    """

    def _log_parse_failure(detail: str) -> None:
        """Log parse failure with the helper's fixed literal detail."""
        logger.debug(
            PROCEDURAL_MEMORY_SKIPPED,
            reason="json_parse_error",
            detail=detail,
        )

    return extract_json_from_llm_response(text, logger_callback=_log_parse_failure)


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

    data = _extract_json(content)
    if data is None:
        logger.warning(PROCEDURAL_MEMORY_SKIPPED, reason="malformed_json", **binding)
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
