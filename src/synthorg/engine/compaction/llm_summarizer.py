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

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.execution_identity import current_execution_identity
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.context_budget import (
    CONTEXT_BUDGET_COMPACTION_LLM_COMPLETED,
    CONTEXT_BUDGET_COMPACTION_LLM_FALLBACK,
    CONTEXT_BUDGET_COMPACTION_LLM_STARTED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig, CompletionResponse

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You compress an AI agent's earlier conversation turns into a concise "
    "summary. Preserve decisions, open questions, constraints, and any "
    "stated uncertainty. Write 3-6 sentences, no preamble.\n\n"
    + untrusted_content_directive((TAG_TASK_DATA,))
)

# Framework-overhead attribution for the compaction summary call.
_SUMMARY_AGENT_ID: NotBlankStr = NotBlankStr("system")


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
        cost_tracker: Sink for the per-call cost record (``None`` makes
            recording a no-op).
    """

    def __init__(
        self,
        *,
        provider: CompletionPort,
        model: str,
        temperature: float,
        max_tokens: int,
        cost_tracker: CostTrackerProtocol | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._cost_tracker = cost_tracker
        self._config = CompletionConfig(
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def summarize(
        self,
        archivable: tuple[ChatMessage, ...],
        *,
        fallback_text: str,
    ) -> str:
        """Summarise the archived batch, falling back to ``fallback_text``.

        The run id (used for log correlation AND the cost-record
        ``task_id``) is read from the ambient ``current_execution_identity()``
        bound at the engine run boundary.

        Args:
            archivable: The conversation messages being archived.
            fallback_text: The Phase-1 text summary used on any failure.

        Returns:
            The LLM summary text, or ``fallback_text`` when the provider
            yields no content or raises a non-critical error.
        """
        transcript = _build_transcript(archivable)
        if not transcript:
            return fallback_text
        identity = current_execution_identity()
        execution_id = (
            identity.execution_id if identity is not None else NotBlankStr("unknown")
        )
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
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=_SUMMARY_AGENT_ID,
                task_id=NotBlankStr(f"compaction:{execution_id}"),
                call_category=LLMCallCategory.SYSTEM,
            ):
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

    Each message body is the agent's own conversation / tool content
    (untrusted), so it is fenced with ``wrap_untrusted`` and the system
    prompt carries the matching directive: a crafted earlier turn cannot
    redirect the summariser.

    Returns:
        The transcript text, or an empty string when no message carries
        content.
    """
    lines = [
        f"{m.role.value}: {wrap_untrusted(TAG_TASK_DATA, m.content.strip())}"
        for m in messages
        if m.content and m.content.strip()
    ]
    return "\n".join(lines)


__all__ = ["CompletionPort", "LLMSummarizer"]
