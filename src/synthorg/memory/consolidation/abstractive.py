"""Abstractive summarizer for sparse memory content.

Uses an LLM (via ``CompletionProvider``) to generate concise summaries
of conversational/narrative memory content.  Falls back to truncation
if the LLM call fails.
"""

import asyncio
from typing import Final

from synthorg.budget.call_category import LLMCallCategory

# ``CostTracker`` is part of ``AbstractiveSummarizer.__init__``'s public
# annotation, so it must resolve at runtime when downstream tooling
# evaluates type hints (DI containers, doc generators).  Importing at
# module top -- not under ``TYPE_CHECKING`` -- keeps the name in module
# globals.
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_UNTRUSTED_ARTIFACT,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.memory.models import MemoryEntry  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.consolidation import (
    DUAL_MODE_ABSTRACTIVE_FALLBACK,
    DUAL_MODE_ABSTRACTIVE_SUMMARY,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import ProviderError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider  # noqa: TC001

logger = get_logger(__name__)

_DEFAULT_MAX_SUMMARY_TOKENS: Final[int] = 200
_DEFAULT_TEMPERATURE: Final[float] = 0.3

_TRUNCATE_LENGTH: Final[int] = 200

_SYSTEM_PROMPT = (
    "You are a memory consolidation assistant. Summarize the following "
    "memory content concisely, preserving key decisions, events, and "
    "learnings. Be factual, specific, and brief.\n\n"
    + untrusted_content_directive((TAG_UNTRUSTED_ARTIFACT,))
)


def _truncate_fallback(content: str) -> str:
    """Truncate content as a fallback when LLM summarization fails."""
    if len(content) <= _TRUNCATE_LENGTH:
        return content
    return content[:_TRUNCATE_LENGTH] + "..."


class AbstractiveSummarizer:
    """LLM-based abstractive summarizer for sparse content.

    Uses a ``CompletionProvider`` to generate concise summaries of
    conversational/narrative memory content.  Falls back to truncation
    if the LLM call fails with a retryable error.

    Args:
        provider: Completion provider for LLM calls.
        model: Model identifier to use for summarization.
        max_summary_tokens: Maximum tokens for the summary response.
        temperature: Sampling temperature for summarization.

    Raises:
        ValueError: If ``model`` is empty or whitespace-only.
    """

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        model: NotBlankStr,
        max_summary_tokens: int = _DEFAULT_MAX_SUMMARY_TOKENS,
        temperature: float = _DEFAULT_TEMPERATURE,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        if not model or not model.strip():
            msg = "model must be a non-blank string"
            raise ValueError(msg)
        self._provider = provider
        self._model = model
        self._cost_tracker = cost_tracker
        self._config = CompletionConfig(
            temperature=temperature,
            max_tokens=max_summary_tokens,
        )

    async def summarize(
        self,
        content: str,
        *,
        agent_id: NotBlankStr | None = None,
    ) -> str:
        """Generate an abstractive summary of the given content.

        Falls back to truncation if the LLM call fails with a
        retryable error or returns empty content.  Non-retryable
        provider errors (authentication, invalid model) propagate.

        Args:
            content: The sparse/conversational text to summarize.
            agent_id: Owning agent for cost attribution.  When
                ``None`` and a ``cost_tracker`` was wired, the call
                is attributed to ``"system"`` with ``task_id``
                ``"system:memory:abstractive"``.

        Returns:
            Summary text.
        """
        try:
            # ``content`` is the raw memory body, which may have
            # absorbed adversarial peer/tool output upstream. Wrap
            # it in a ``<untrusted-artifact>`` fence; the system
            # prompt carries the matching directive.
            messages = [
                ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
                ChatMessage(
                    role=MessageRole.USER,
                    content=wrap_untrusted(TAG_UNTRUSTED_ARTIFACT, content),
                ),
            ]
            attribution_agent: NotBlankStr = agent_id or NotBlankStr("system")
            attribution_task: NotBlankStr = NotBlankStr("system:memory:abstractive")
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=attribution_agent,
                task_id=attribution_task,
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages,
                    self._model,
                    config=self._config,
                )
            if response.content and response.content.strip():
                logger.debug(
                    DUAL_MODE_ABSTRACTIVE_SUMMARY,
                    content_length=len(content),
                    summary_length=len(response.content),
                    model=self._model,
                )
                return response.content.strip()
        except MemoryError, RecursionError:
            raise
        except ProviderError as exc:
            if not exc.is_retryable:
                logger.warning(
                    DUAL_MODE_ABSTRACTIVE_FALLBACK,
                    content_length=len(content),
                    error=safe_error_description(exc),
                    error_type=type(exc).__name__,
                    retryable=False,
                )
                raise
            logger.warning(
                DUAL_MODE_ABSTRACTIVE_FALLBACK,
                content_length=len(content),
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
            )
            return _truncate_fallback(content)
        except Exception as exc:
            logger.warning(
                DUAL_MODE_ABSTRACTIVE_FALLBACK,
                content_length=len(content),
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
            )
            return _truncate_fallback(content)

        # Fallback: empty/whitespace-only LLM response
        logger.debug(
            DUAL_MODE_ABSTRACTIVE_FALLBACK,
            content_length=len(content),
            reason="empty_response",
        )
        return _truncate_fallback(content)

    async def summarize_batch(
        self,
        entries: tuple[MemoryEntry, ...],
    ) -> tuple[tuple[NotBlankStr, str], ...]:
        """Summarize multiple entries concurrently.

        Each entry is summarized independently via ``asyncio.TaskGroup``.
        Failures for individual entries fall back to truncation without
        aborting the batch.

        Args:
            entries: Memory entries to summarize.

        Returns:
            Tuple of ``(entry_id, summary)`` pairs in input order.
        """
        if not entries:
            return ()

        results: dict[NotBlankStr, str] = {}
        async with asyncio.TaskGroup() as tg:
            tasks: dict[NotBlankStr, asyncio.Task[str]] = {}
            for entry in entries:
                tasks[entry.id] = tg.create_task(
                    self.summarize(entry.content, agent_id=entry.agent_id),
                )

        for entry_id, task in tasks.items():
            results[entry_id] = task.result()

        return tuple((entry.id, results[entry.id]) for entry in entries)
