"""Experience compressor protocol and LLM implementation.

Defines the ``ExperienceCompressor`` protocol for compressing raw
execution traces into concise strategic learnings (GEMS two-tier
architecture).
"""

import builtins
import hashlib
import json
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.budget.call_category import LLMCallCategory

# ``CostTracker``, ``ExperienceCompressorConfig`` and
# ``CompletionProvider`` are part of ``LLMExperienceCompressor.__init__``'s
# public annotation, so they must resolve at runtime when downstream
# tooling evaluates type hints (DI containers, doc generators).
from synthorg.budget.tracker import CostTracker
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    TAG_TOOL_RESULT,
    TAG_UNTRUSTED_ARTIFACT,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.memory.consolidation.config import (
    ExperienceCompressorConfig,
)
from synthorg.memory.consolidation.models import (
    CompressedExperience,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.consolidation import (
    EXPERIENCE_COMPRESSED,
    EXPERIENCE_COMPRESSION_FAILED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)


@runtime_checkable
class ExperienceCompressor(Protocol):
    """Compresses raw traces into concise experiences.

    Fidelity target: compressed summaries must reproduce at least 80%
    of strategic decisions from raw traces on a held-out test set.
    """

    async def compress(  # noqa: PLR0913
        self,
        prompt: NotBlankStr,
        output: NotBlankStr,
        verification_feedback: NotBlankStr | None,
        reasoning_trace: tuple[NotBlankStr, ...],
        memory_context: tuple[MemoryEntry, ...],
        *,
        agent_id: NotBlankStr = "unknown",
        source_artifact_ids: tuple[NotBlankStr, ...],
    ) -> CompressedExperience:
        """Compress a single raw experience into strategic learnings.

        Args:
            prompt: Raw prompt sent to the agent.
            output: Raw output produced by the agent.
            verification_feedback: Verification result text
                (``None`` when no verification was performed).
            reasoning_trace: Step-by-step reasoning trace entries.
            memory_context: Related memories for compression context.
            agent_id: Agent owning the experience (for provenance).
            source_artifact_ids: IDs of raw artifacts being compressed.

        Returns:
            Compressed experience with strategic decisions and
            applicable contexts.

        Raises:
            Exception: On LLM call failure (caller decides fallback
                behaviour).
        """
        ...


_COMPRESSOR_SYSTEM_PROMPT = """\
You are a memory compressor. Given a raw execution trace (prompt, \
output, verification feedback, reasoning steps), extract the strategic \
learnings.

Respond with JSON:
{{
  "strategic_decisions": ["what worked or didn't", ...],
  "applicable_contexts": ["when this applies", ...]
}}

Focus on:
- Key decisions that led to success or failure
- Reusable patterns and anti-patterns
- Context-specific applicability

Be concise. Each decision should be one sentence. \
Each context should describe when the lesson applies.

""" + untrusted_content_directive(
    (TAG_TASK_DATA, TAG_TOOL_RESULT, TAG_UNTRUSTED_ARTIFACT)
)

_COMPRESSOR_VERSION = "llm-v1"


class CompressionLLMResponse(BaseModel):
    """Typed view of the experience-compressor LLM JSON reply.

    Routing the parsed JSON through this model replaces the manual
    list-of-non-blank-strings checks: ``NotBlankStr`` rejects blank
    entries and ``extra="forbid"`` rejects a hallucinated response with
    keys beyond the two the prompt requests. Both fields default to empty
    so a missing key still surfaces as the explicit "no strategic
    decisions" guard downstream rather than a validation error.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategic_decisions: tuple[NotBlankStr, ...] = Field(default=())
    applicable_contexts: tuple[NotBlankStr, ...] = Field(default=())


class LLMExperienceCompressor:
    """LLM-based experience compressor (GEMS strategy).

    Uses a medium-tier model to compress raw execution traces into
    strategic learnings with applicable contexts.

    Args:
        provider: Completion provider for LLM calls.
        model: Model identifier (medium-tier recommended).
        config: Compressor configuration.
    """

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model: NotBlankStr,
        config: ExperienceCompressorConfig,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._config = config
        self._cost_tracker = cost_tracker

    async def compress(  # noqa: PLR0913
        self,
        prompt: NotBlankStr,
        output: NotBlankStr,
        verification_feedback: NotBlankStr | None,
        reasoning_trace: tuple[NotBlankStr, ...],
        memory_context: tuple[MemoryEntry, ...],
        *,
        agent_id: NotBlankStr = "unknown",
        source_artifact_ids: tuple[NotBlankStr, ...],
    ) -> CompressedExperience:
        """Compress a single raw experience via LLM.

        Args:
            prompt: Raw prompt sent to the agent.
            output: Raw output produced by the agent.
            verification_feedback: Verification result text.
            reasoning_trace: Step-by-step reasoning trace entries.
            memory_context: Related memories for compression context.
            agent_id: Agent owning the experience (for provenance).
            source_artifact_ids: IDs of raw artifacts being compressed.

        Returns:
            Compressed experience with strategic decisions.

        Raises:
            ValueError: If ``source_artifact_ids`` is empty (fail fast
                before spending an LLM call), or if the LLM response
                is malformed.
            Exception: On LLM call failure (not silently swallowed).
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
            TypeError: If an argument has an unexpected type.
        """
        if not source_artifact_ids:
            msg = (
                "source_artifact_ids must contain at least one entry "
                "(provenance required before compression)"
            )
            logger.warning(
                EXPERIENCE_COMPRESSION_FAILED,
                error=msg,
                agent_id=agent_id,
            )
            raise ValueError(msg)

        # Raw artifact length excludes the optional ``memory_context``
        # so ``compression_ratio`` is a stable size comparison between
        # the source (prompt + output + verification + reasoning) and
        # the compressed LLM response.
        raw_artifact_parts = [prompt, output]
        if verification_feedback:
            raw_artifact_parts.append(verification_feedback)
        if reasoning_trace:
            raw_artifact_parts.extend(reasoning_trace)
        raw_len = sum(len(part) for part in raw_artifact_parts)

        # Every untrusted field is wrapped in its tag so the
        # compressor model treats them as data. ``prompt`` is the
        # operator-supplied task content; ``output`` and
        # ``verification_feedback`` may carry adversarial peer/tool
        # output; ``reasoning_trace`` carries arbitrary intermediate
        # tool outputs; ``memory_context`` is prior memories that may
        # have been seeded by attacker-influenced traces.
        user_parts = [
            f"## Prompt\n{wrap_untrusted(TAG_TASK_DATA, prompt)}",
            f"## Output\n{wrap_untrusted(TAG_UNTRUSTED_ARTIFACT, output)}",
        ]
        if verification_feedback:
            user_parts.append(
                "## Verification\n"
                + wrap_untrusted(TAG_UNTRUSTED_ARTIFACT, verification_feedback),
            )
        if reasoning_trace:
            trace_text = "\n".join(f"- {step}" for step in reasoning_trace)
            user_parts.append(
                f"## Reasoning\n{wrap_untrusted(TAG_TOOL_RESULT, trace_text)}"
            )
        if memory_context:
            context_text = "\n".join(f"- {m.content[:200]}" for m in memory_context[:5])
            user_parts.append(
                "## Memory Context\n"
                + wrap_untrusted(TAG_UNTRUSTED_ARTIFACT, context_text),
            )

        user_content = "\n\n".join(user_parts)

        messages: list[ChatMessage] = [
            ChatMessage(
                role=MessageRole.SYSTEM,
                content=_COMPRESSOR_SYSTEM_PROMPT,
            ),
            ChatMessage(role=MessageRole.USER, content=user_content),
        ]

        completion_config = CompletionConfig(
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=agent_id,
                task_id=NotBlankStr(f"system:memory:compress:{source_artifact_ids[0]}"),
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages,
                    self._model,
                    config=completion_config,
                )
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                EXPERIENCE_COMPRESSION_FAILED,
                context="provider call failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                model=self._model,
                agent_id=agent_id,
                source_artifact_ids=list(source_artifact_ids),
            )
            raise
        if response.content is None:
            msg = "LLM returned empty content for compression"
            logger.warning(EXPERIENCE_COMPRESSION_FAILED, error=msg)
            raise ValueError(msg)

        try:
            parsed = json.loads(response.content)
        except json.JSONDecodeError as exc:
            logger.warning(
                EXPERIENCE_COMPRESSION_FAILED,
                context="JSON decode failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                content_length=len(response.content),
                content_hash=hashlib.sha256(
                    response.content.encode("utf-8"),
                ).hexdigest()[:16],
            )
            msg = f"malformed compression output: {safe_error_description(exc)}"
            raise ValueError(msg) from exc
        try:
            response_model = CompressionLLMResponse.model_validate(parsed)
        except ValidationError as exc:
            msg = "compression output failed validation"
            logger.warning(
                EXPERIENCE_COMPRESSION_FAILED,
                error=msg,
                error_type=type(exc).__name__,
                error_detail=safe_error_description(exc),
            )
            raise ValueError(msg) from exc
        decisions = response_model.strategic_decisions
        contexts = response_model.applicable_contexts

        if not decisions:
            msg = "LLM produced no strategic decisions"
            logger.warning(EXPERIENCE_COMPRESSION_FAILED, error=msg)
            raise ValueError(msg)

        compressed_len = len(response.content)
        ratio = compressed_len / max(raw_len, 1)
        ratio = min(ratio, 1.0)
        # ``CompressedExperience.compression_ratio`` requires ``gt=0``.
        # Clamp extreme compressions (e.g. empty raw artifact) to a
        # 0.01 floor to satisfy that invariant without discarding
        # legitimate aggressive compressions.
        compression_ratio = max(ratio, 0.01)

        try:
            experience = CompressedExperience(
                id=str(uuid4()),
                agent_id=agent_id,
                strategic_decisions=decisions,
                applicable_contexts=contexts,
                source_artifact_ids=source_artifact_ids,
                compression_ratio=compression_ratio,
                compressor_version=_COMPRESSOR_VERSION,
                metadata=MemoryMetadata(
                    tags=("compressed_experience",),
                ),
                created_at=datetime.now(UTC),
            )
        except builtins.MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                EXPERIENCE_COMPRESSION_FAILED,
                context="CompressedExperience validation failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                agent_id=agent_id,
                source_artifact_count=len(source_artifact_ids),
            )
            raise
        logger.info(
            EXPERIENCE_COMPRESSED,
            decisions_count=len(decisions),
            contexts_count=len(contexts),
            compression_ratio=experience.compression_ratio,
        )
        return experience
