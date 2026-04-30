"""LLM-based proposer for procedural memory entries.

Uses a SEPARATE completion provider call (not the failed agent) to
analyse a structured failure payload and produce a procedural memory
proposal.  Follows the ``AbstractiveSummarizer`` error-handling
pattern from ``memory.consolidation.abstractive``.
"""

import json
import re
from typing import Any

from pydantic import ValidationError

from synthorg.budget.call_category import LLMCallCategory

# ``CostTracker`` is part of ``ProceduralMemoryProposer.__init__``'s
# annotation, so it must resolve at runtime when downstream tooling
# evaluates type hints (DI containers, doc generators).  Importing at
# module top -- not under ``TYPE_CHECKING`` -- keeps the name in module
# globals.
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    TAG_TOOL_RESULT,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.memory.procedural.models import (
    FailureAnalysisPayload,
    ProceduralMemoryConfig,
    ProceduralMemoryProposal,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.procedural_memory import (
    PROCEDURAL_MEMORY_LOW_CONFIDENCE,
    PROCEDURAL_MEMORY_PROPOSED,
    PROCEDURAL_MEMORY_PROPOSER_INIT,
    PROCEDURAL_MEMORY_SKIPPED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import ProviderError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider  # noqa: TC001

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a failure analysis assistant. Given a structured description "
    "of an agent task failure, produce a procedural memory entry that "
    "helps future agents avoid the same failure.\n\n"
    "Respond with a JSON object containing exactly these fields:\n"
    '- "discovery": A one-sentence summary (~100 tokens) for retrieval.\n'
    '- "condition": When this knowledge should be applied.\n'
    '- "action": What to do differently next time.\n'
    '- "rationale": Why this approach helps.\n'
    '- "execution_steps": Ordered list of concrete steps to follow '
    '(e.g. ["Step 1", "Step 2"]).\n'
    '- "confidence": Your confidence in this proposal (0.0-1.0).\n'
    '- "tags": List of semantic tags (e.g. ["timeout", "tool_failure"]).\n\n'
    "Respond ONLY with the JSON object, no markdown fences or explanation.\n\n"
    + untrusted_content_directive((TAG_TASK_DATA, TAG_TOOL_RESULT))
)

_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from LLM response text.

    Handles plain JSON and markdown-fenced JSON blocks.
    Returns ``None`` on parse failure.
    """
    stripped = text.strip()
    if not stripped:
        return None

    # Try stripping markdown fences first.
    match = _JSON_FENCE_PATTERN.search(stripped)
    candidate = match.group(1).strip() if match else stripped

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        logger.debug(
            PROCEDURAL_MEMORY_SKIPPED,
            reason="json_parse_error",
            detail=str(exc),
        )
        return None

    if not isinstance(parsed, dict):
        return None
    return parsed


def _build_user_message(payload: FailureAnalysisPayload) -> str:
    """Format the payload into a user message for the proposer LLM.

    SEC-1: every attacker-controllable field (task title, description,
    error message, termination reason, tool list) is wrapped in its
    appropriate ``TAG_*`` fence so the proposer LLM treats them as
    data.  The matching ``untrusted_content_directive`` is appended
    to ``_SYSTEM_PROMPT``.
    """
    tools = ", ".join(payload.tool_calls_made) if payload.tool_calls_made else "none"
    task_block = (
        f"Title: {payload.task_title}\n"
        f"Description: {payload.task_description}\n"
        f"Type: {payload.task_type.value}\n"
        f"Termination: {payload.termination_reason}"
    )
    return (
        "## Failure context\n"
        + wrap_untrusted(TAG_TASK_DATA, task_block)
        + "\n\n## Error message\n"
        + wrap_untrusted(TAG_TASK_DATA, payload.error_message)
        + "\n\n## Tool calls made\n"
        + wrap_untrusted(TAG_TOOL_RESULT, tools)
        + "\n\n## Run metadata (trusted)\n"
        f"Recovery strategy: {payload.strategy_type}\n"
        f"Turns completed: {payload.turn_count}\n"
        f"Retry {payload.retry_count}/{payload.max_retries} "
        f"(can reassign: {payload.can_reassign})"
    )


class ProceduralMemoryProposer:
    """Generates procedural memory proposals from failure analysis.

    Uses a separate LLM call to analyse a structured failure payload
    and produce a ``ProceduralMemoryProposal``.  Non-retryable
    provider errors propagate; retryable errors return ``None``.

    Args:
        provider: Completion provider for the proposer LLM call.
        config: Procedural memory configuration.
        cost_tracker: Optional :class:`CostTracker`.  When wired, the
            provider chokepoint emits a ``CostRecord`` for each
            proposer call attributed to the owning agent and a
            per-task ``task_id``; when ``None``, the scope is a
            silent no-op (used by tests and probes).
    """

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        config: ProceduralMemoryConfig,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._cost_tracker = cost_tracker
        self._completion_config = CompletionConfig(
            temperature=config.temperature,
            max_tokens=config.max_tokens,
        )
        logger.debug(
            PROCEDURAL_MEMORY_PROPOSER_INIT,
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            min_confidence=config.min_confidence,
        )

    async def propose(
        self,
        payload: FailureAnalysisPayload,
    ) -> ProceduralMemoryProposal | None:
        """Analyse failure and propose a procedural memory entry.

        Returns ``None`` when the LLM response is empty, malformed,
        or below the confidence threshold.  Non-retryable provider
        errors propagate to the caller.

        Args:
            payload: Structured failure context.

        Returns:
            A validated proposal, or ``None`` if skipped.
        """
        try:
            messages = [
                ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
                ChatMessage(
                    role=MessageRole.USER,
                    content=_build_user_message(payload),
                ),
            ]
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=NotBlankStr("system"),
                task_id=NotBlankStr(f"system:procedural:propose:{payload.task_id}"),
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages,
                    self._config.model,
                    config=self._completion_config,
                )
        except MemoryError, RecursionError:
            raise
        except ProviderError as exc:
            if not exc.is_retryable:
                raise
            logger.warning(
                PROCEDURAL_MEMORY_SKIPPED,
                task_id=payload.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="retryable_provider_error",
            )
            return None
        except Exception as exc:
            logger.warning(
                PROCEDURAL_MEMORY_SKIPPED,
                task_id=payload.task_id,
                error=f"{type(exc).__name__}: {exc}",
                reason="unexpected_error",
                exc_info=True,
            )
            return None

        return self._parse_response(response.content, payload.task_id)

    def _parse_response(
        self,
        content: str | None,
        task_id: str,
    ) -> ProceduralMemoryProposal | None:
        """Parse and validate the LLM response into a proposal."""
        if not content or not content.strip():
            logger.debug(
                PROCEDURAL_MEMORY_SKIPPED,
                task_id=task_id,
                reason="empty_response",
            )
            return None

        data = _extract_json(content)
        if data is None:
            logger.warning(
                PROCEDURAL_MEMORY_SKIPPED,
                task_id=task_id,
                reason="malformed_json",
            )
            return None

        try:
            proposal = ProceduralMemoryProposal(**data)
        except ValidationError as exc:
            logger.warning(
                PROCEDURAL_MEMORY_SKIPPED,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="validation_failed",
            )
            return None

        if proposal.confidence < self._config.min_confidence:
            logger.info(
                PROCEDURAL_MEMORY_LOW_CONFIDENCE,
                task_id=task_id,
                confidence=proposal.confidence,
                min_confidence=self._config.min_confidence,
            )
            return None

        logger.info(
            PROCEDURAL_MEMORY_PROPOSED,
            task_id=task_id,
            confidence=proposal.confidence,
            tags=proposal.tags,
        )
        return proposal
