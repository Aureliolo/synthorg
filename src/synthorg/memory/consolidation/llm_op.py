"""LLM synthesis consolidation op for the axis split (ADR-0005).

Extracted verbatim (behaviour-preserving) from
``LLMConsolidationStrategy``: per-group synthesis with trajectory
context, prompt-cap truncation accounting, concatenation fallback,
and the best-effort per-delete loop. The cross-group
``asyncio.TaskGroup`` fan-out + ``except*`` unwrap that the monolith
did in ``_run_groups`` now lives in
:class:`~synthorg.memory.consolidation.composite.CompositeConsolidationStrategy`
(constructed with ``parallel=True`` for LLM). Trajectory context is
fetched once per run via :meth:`LLMSynthesisOp.prepare`, exactly as
the monolith fetched it before its group loop.
"""

import asyncio
from enum import StrEnum
from typing import TYPE_CHECKING

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.core.enums import MemoryCategory  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_MEMORY_ENTRY,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.memory.consolidation.axis import (
    ConsolidationContext,
    OpResult,
    SelectionGroup,
)
from synthorg.memory.consolidation.config import LLMConsolidationConfig
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.consolidation import (
    LLM_STRATEGY_ERROR,
    LLM_STRATEGY_FALLBACK,
    LLM_STRATEGY_SYNTHESIZED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import ProviderError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.resilience.errors import RetryExhaustedError

if TYPE_CHECKING:
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)


class SynthesisOutcome(StrEnum):
    """Outcome of an LLM synthesis attempt.

    Makes the synthesis result explicit instead of a bare ``bool``.
    """

    LLM_SYNTHESIZED = "llm_synthesized"
    """LLM returned non-empty content -- real synthesis occurred."""

    CONCAT_FALLBACK = "concat_fallback"
    """LLM call failed or returned empty -- concatenation fallback used."""


_DISTILLATION_TAG: NotBlankStr = "distillation"
_LLM_SYNTHESIZED_TAG: NotBlankStr = "llm-synthesized"
_CONCAT_FALLBACK_TAG: NotBlankStr = "concat-fallback"

_BASE_SYSTEM_PROMPT = (
    "You are a memory consolidation assistant. You will receive "
    f"multiple memory entries from the same category, each enclosed "
    f"in <{TAG_MEMORY_ENTRY}>...</{TAG_MEMORY_ENTRY}> tags. Your task "
    "is to:\n"
    "1. Identify duplicate or overlapping information across entries\n"
    "2. Merge semantically related facts into concise statements\n"
    "3. Preserve ALL unique information: specific details, IDs, dates, "
    "names, decisions, and outcomes\n"
    "4. Return a single synthesized summary that is shorter than the "
    "combined input but retains all distinct facts\n\n"
    "Respond with ONLY the synthesized summary, nothing else.\n\n"
    + untrusted_content_directive((TAG_MEMORY_ENTRY,))
)


class LLMSynthesisOp:
    """LLM synthesis op -- ``LLMConsolidationStrategy`` per-group logic.

    Args:
        backend: Memory backend for storing summaries + reading
            distillation entries + deleting originals.
        provider: Completion provider for synthesis calls.
        model: Model identifier for the synthesis LLM.
        config: LLM consolidation configuration.
        cost_tracker: Optional cost tracker for synthesis attribution.
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        provider: CompletionProvider,
        model: NotBlankStr,
        config: LLMConsolidationConfig | None = None,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        cfg = config if config is not None else LLMConsolidationConfig()
        self._backend = backend
        self._provider = provider
        self._model = model
        self._config = cfg
        self._cost_tracker = cost_tracker
        self._completion_config = CompletionConfig(
            temperature=cfg.temperature,
            max_tokens=cfg.max_summary_tokens,
        )

    async def prepare(
        self,
        agent_id: NotBlankStr,
    ) -> ConsolidationContext:
        """Fetch distillation trajectory context once per run.

        Byte-identical with ``LLMConsolidationStrategy``'s pre-group
        ``_fetch_trajectory_context``: best-effort, degrades to empty
        context on non-system failure (logged WARNING), propagates
        ``MemoryError`` / ``RecursionError``.
        """
        return ConsolidationContext(
            agent_id=agent_id,
            trajectory_context=await self._fetch_trajectory_context(agent_id),
        )

    async def consolidate(
        self,
        group: SelectionGroup,
        *,
        context: ConsolidationContext,
    ) -> OpResult:
        """Synthesize + store + best-effort delete (LLM semantics).

        Mirrors ``LLMConsolidationStrategy._process_group``: synthesize
        and store the summary FIRST, then delete the originals, so a
        synthesis/store failure loses no data. Only entries the prompt
        cap admitted are eligible for deletion.
        """
        synthesized, outcome, summarized = await self._synthesize(
            group.to_remove,
            agent_id=context.agent_id,
            category=group.category,
            trajectory_context=context.trajectory_context,
        )
        new_id = await self._store_summary(
            synthesized,
            category=group.category,
            agent_id=context.agent_id,
            outcome=outcome,
        )
        if outcome == SynthesisOutcome.LLM_SYNTHESIZED:
            logger.info(
                LLM_STRATEGY_SYNTHESIZED,
                agent_id=context.agent_id,
                category=group.category.value,
                entry_count=len(summarized),
                summary_id=new_id,
                model=self._model,
                trajectory_context_count=len(context.trajectory_context),
            )
        removed_ids = await self._delete_consolidated(
            summarized,
            agent_id=context.agent_id,
            category=group.category,
        )
        return OpResult(summary_id=new_id, removed_ids=tuple(removed_ids))

    async def _fetch_trajectory_context(
        self,
        agent_id: NotBlankStr,
    ) -> tuple[MemoryEntry, ...]:
        """Verbatim ``LLMConsolidationStrategy._fetch_trajectory_context``."""
        if not self._config.include_distillation_context:
            return ()
        try:
            query = MemoryQuery(
                tags=(_DISTILLATION_TAG,),
                limit=self._config.max_trajectory_context_entries * 4,
            )
            raw = await self._backend.retrieve(agent_id, query)
            by_recency = sorted(
                raw,
                key=lambda e: e.created_at,
                reverse=True,
            )
            return tuple(by_recency[: self._config.max_trajectory_context_entries])
        except MemoryError, RecursionError:
            logger.error(
                LLM_STRATEGY_ERROR,
                agent_id=agent_id,
                reason="system_error_in_trajectory_fetch",
                error_type="system",
            )
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                LLM_STRATEGY_FALLBACK,
                agent_id=agent_id,
                reason="distillation_lookup_failed",
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
            )
            return ()

    async def _store_summary(
        self,
        content: str,
        *,
        category: MemoryCategory,
        agent_id: NotBlankStr,
        outcome: SynthesisOutcome,
    ) -> NotBlankStr:
        """Verbatim ``LLMConsolidationStrategy._store_summary``."""
        tag = (
            _LLM_SYNTHESIZED_TAG
            if outcome == SynthesisOutcome.LLM_SYNTHESIZED
            else _CONCAT_FALLBACK_TAG
        )
        store_request = MemoryStoreRequest(
            category=category,
            content=content,
            metadata=MemoryMetadata(
                source="consolidation",
                tags=("consolidated", tag),
            ),
        )
        return await self._backend.store(agent_id, store_request)

    async def _delete_consolidated(
        self,
        to_remove: tuple[MemoryEntry, ...],
        *,
        agent_id: NotBlankStr,
        category: MemoryCategory,
    ) -> list[NotBlankStr]:
        """Verbatim ``LLMConsolidationStrategy._delete_consolidated``."""
        removed_ids: list[NotBlankStr] = []
        for entry in to_remove:
            try:
                await self._backend.delete(agent_id, entry.id)
            except MemoryError, RecursionError:
                logger.error(
                    LLM_STRATEGY_ERROR,
                    agent_id=agent_id,
                    category=category.value,
                    entry_id=entry.id,
                    reason="system_error_in_delete",
                    error_type="system",
                )
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    LLM_STRATEGY_ERROR,
                    agent_id=agent_id,
                    category=category.value,
                    entry_id=entry.id,
                    reason="delete_failed",
                    error=safe_error_description(exc),
                    error_type=type(exc).__name__,
                )
                continue
            removed_ids.append(entry.id)
        return removed_ids

    async def _synthesize(
        self,
        entries: tuple[MemoryEntry, ...],
        *,
        agent_id: NotBlankStr,
        category: MemoryCategory,
        trajectory_context: tuple[MemoryEntry, ...],
    ) -> tuple[str, SynthesisOutcome, tuple[MemoryEntry, ...]]:
        """Verbatim ``LLMConsolidationStrategy._synthesize``."""
        user_content, summarized = self._build_user_prompt(entries, agent_id, category)
        system_prompt = self._build_system_prompt(trajectory_context)
        response_content = await self._call_llm(
            system_prompt,
            user_content,
            agent_id=agent_id,
            category=category,
            entry_count=len(summarized),
        )
        if response_content is not None:
            return response_content, SynthesisOutcome.LLM_SYNTHESIZED, summarized
        fallback = self._fallback_summary(entries)
        return fallback, SynthesisOutcome.CONCAT_FALLBACK, entries

    def _build_user_prompt(
        self,
        entries: tuple[MemoryEntry, ...],
        agent_id: NotBlankStr,
        category: MemoryCategory,
    ) -> tuple[str, tuple[MemoryEntry, ...]]:
        """Verbatim ``LLMConsolidationStrategy._build_user_prompt``."""
        parts: list[str] = []
        included: list[MemoryEntry] = []
        total_chars = 0
        for entry in entries:
            snippet = entry.content[: self._config.max_entry_input_chars]
            body = f"category: {entry.category.value}\n{snippet}"
            piece = wrap_untrusted(TAG_MEMORY_ENTRY, body)
            if total_chars + len(piece) > self._config.max_total_user_content_chars:
                break
            parts.append(piece)
            included.append(entry)
            total_chars += len(piece) + 1
        dropped = len(entries) - len(included)
        if dropped > 0:
            logger.warning(
                LLM_STRATEGY_FALLBACK,
                agent_id=agent_id,
                category=category.value,
                reason="user_prompt_truncated",
                kept_entries=len(included),
                dropped_entries=dropped,
                total_chars=total_chars,
            )
        return "\n".join(parts), tuple(included)

    async def _call_llm(
        self,
        system_prompt: str,
        user_content: str,
        *,
        agent_id: NotBlankStr,
        category: MemoryCategory,
        entry_count: int,
    ) -> str | None:
        """Verbatim ``LLMConsolidationStrategy._call_llm``."""
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
            ChatMessage(role=MessageRole.USER, content=user_content),
        ]
        try:
            async with cost_recording_scope(
                cost_tracker=self._cost_tracker,
                agent_id=agent_id,
                task_id=NotBlankStr(f"system:memory:consolidate:{category.value}"),
                call_category=LLMCallCategory.SYSTEM,
            ):
                response = await self._provider.complete(
                    messages,
                    self._model,
                    config=self._completion_config,
                )
        except MemoryError, RecursionError:
            logger.error(
                LLM_STRATEGY_ERROR,
                agent_id=agent_id,
                category=category.value,
                model=self._model,
                reason="system_error",
            )
            raise
        except RetryExhaustedError as exc:
            logger.warning(
                LLM_STRATEGY_FALLBACK,
                agent_id=agent_id,
                category=category.value,
                entry_count=entry_count,
                model=self._model,
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
                reason="retry_exhausted",
            )
            return None
        except ProviderError as exc:
            return self._handle_provider_error(
                exc,
                agent_id=agent_id,
                category=category,
                entry_count=entry_count,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                LLM_STRATEGY_FALLBACK,
                agent_id=agent_id,
                category=category.value,
                entry_count=entry_count,
                model=self._model,
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
                reason="unexpected_error",
            )
            return None

        if response.content and response.content.strip():
            return response.content.strip()
        logger.warning(
            LLM_STRATEGY_FALLBACK,
            agent_id=agent_id,
            category=category.value,
            entry_count=entry_count,
            model=self._model,
            reason="empty_response",
        )
        return None

    def _handle_provider_error(
        self,
        exc: ProviderError,
        *,
        agent_id: NotBlankStr,
        category: MemoryCategory,
        entry_count: int,
    ) -> str | None:
        """Verbatim ``LLMConsolidationStrategy._handle_provider_error``."""
        if exc.is_retryable:
            logger.warning(
                LLM_STRATEGY_FALLBACK,
                agent_id=agent_id,
                category=category.value,
                entry_count=entry_count,
                model=self._model,
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
                reason="retryable_provider_error",
            )
            return None
        logger.error(
            LLM_STRATEGY_ERROR,
            agent_id=agent_id,
            category=category.value,
            entry_count=entry_count,
            model=self._model,
            error=safe_error_description(exc),
            error_type=type(exc).__name__,
            reason="non_retryable_provider_error",
        )
        raise exc

    def _build_system_prompt(
        self,
        trajectory_context: tuple[MemoryEntry, ...],
    ) -> str:
        """Verbatim ``LLMConsolidationStrategy._build_system_prompt``."""
        if not trajectory_context:
            return _BASE_SYSTEM_PROMPT
        context_lines = ["\nRecent trajectory context (for disambiguation only):"]
        for entry in trajectory_context:
            snippet = entry.content[: self._config.max_trajectory_chars_per_entry]
            context_lines.append(
                "- " + wrap_untrusted(TAG_MEMORY_ENTRY, snippet),
            )
        return _BASE_SYSTEM_PROMPT + "\n" + "\n".join(context_lines)

    def _fallback_summary(self, entries: tuple[MemoryEntry, ...]) -> str:
        """Verbatim ``LLMConsolidationStrategy._fallback_summary``."""
        if not entries:
            return ""
        lines = [f"Consolidated {entries[0].category.value} memories:"]
        for entry in entries:
            truncated = (
                entry.content[: self._config.fallback_truncate_length] + "..."
                if len(entry.content) > self._config.fallback_truncate_length
                else entry.content
            )
            lines.append(f"- {truncated}")
        return "\n".join(lines)
