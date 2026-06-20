# module-kind: adapter
"""LLM-backed compaction summariser (Phase-2).

Replaces the Phase-1 snippet-join text summary with a semantic summary
produced by a completion provider. Resilience (retry / rate limit) stays
in the provider base, never here. Any provider failure -- empty content,
retryable or non-retryable error -- degrades to the Phase-1 text summary
that the caller passes in, logged as a fallback so the downgrade is never
silent.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.context_budget import (
    CONTEXT_BUDGET_COMPACTION_LLM_COMPLETED,
    CONTEXT_BUDGET_COMPACTION_LLM_FALLBACK,
    CONTEXT_BUDGET_COMPACTION_LLM_STARTED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig, CompletionResponse

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You compress an AI agent's earlier conversation turns into a concise "
    "summary. Preserve decisions, open questions, constraints, and any "
    "stated uncertainty. Write 3-6 sentences, no preamble."
)


@runtime_checkable
class CompletionPort(Protocol):
    """Narrow completion seam the summariser depends on.

    Structurally satisfied by ``BaseCompletionProvider`` subclasses, so
    the compaction module never imports a concrete provider.
    """

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Return a completion for ``messages`` against ``model``."""
        ...


class LLMSummarizer:
    """Summarises an archived turn batch via a completion provider.

    Args:
        provider: Completion port used to generate the summary.
        model: Model id for the summary call.
        temperature: Sampling temperature (pinned explicitly).
        max_tokens: Max tokens for the summary response.
    """

    def __init__(
        self,
        *,
        provider: CompletionPort,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> None:
        self._provider = provider
        self._model = model
        self._config = CompletionConfig(
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def summarize(
        self,
        archivable: tuple[ChatMessage, ...],
        *,
        execution_id: str,
        fallback_text: str,
    ) -> str:
        """Summarise the archived batch, falling back to ``fallback_text``.

        Args:
            archivable: The conversation messages being archived.
            execution_id: Execution id for log correlation.
            fallback_text: The Phase-1 text summary used on any failure.

        Returns:
            The LLM summary text, or ``fallback_text`` when the provider
            yields no content or raises a non-critical error.
        """
        transcript = _build_transcript(archivable)
        if not transcript:
            return fallback_text
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
            ChatMessage(role=MessageRole.USER, content=transcript),
        ]
        logger.debug(
            CONTEXT_BUDGET_COMPACTION_LLM_STARTED,
            execution_id=execution_id,
            archived_count=len(archivable),
            model=self._model,
        )
        try:
            response = await self._provider.complete(
                messages, self._model, config=self._config
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                CONTEXT_BUDGET_COMPACTION_LLM_FALLBACK,
                execution_id=execution_id,
                reason="provider_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return fallback_text
        content = (response.content or "").strip()
        if not content:
            logger.warning(
                CONTEXT_BUDGET_COMPACTION_LLM_FALLBACK,
                execution_id=execution_id,
                reason="empty_content",
            )
            return fallback_text
        logger.info(
            CONTEXT_BUDGET_COMPACTION_LLM_COMPLETED,
            execution_id=execution_id,
            summary_chars=len(content),
        )
        return content


def _build_transcript(messages: tuple[ChatMessage, ...]) -> str:
    """Join archived messages into a role-tagged transcript.

    Returns:
        The transcript text, or an empty string when no message carries
        content.
    """
    lines = [
        f"{m.role.value}: {m.content.strip()}"
        for m in messages
        if m.content and m.content.strip()
    ]
    return "\n".join(lines)


__all__ = ["CompletionPort", "LLMSummarizer"]
