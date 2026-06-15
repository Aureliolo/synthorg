"""LLM synthesis consolidation op for the axis split (ADR-0005).

Per-group synthesis with trajectory context, prompt-cap truncation
accounting, concatenation fallback, and a best-effort per-delete loop.
Trajectory context is fetched once per run via
:meth:`LLMSynthesisOp.prepare`; cross-group ``asyncio.TaskGroup``
fan-out + ``except*`` unwrap is owned by
:class:`~synthorg.memory.consolidation.composite.CompositeConsolidationStrategy`
(constructed with ``parallel=True`` for LLM).
"""

import asyncio
import builtins
from enum import StrEnum

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker import CostTracker
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_MEMORY_ENTRY,
    wrap_untrusted,
)
from synthorg.memory.consolidation.axis import (
    ConsolidationContext,
    OpResult,
    SelectionGroup,
)
from synthorg.memory.consolidation.config import LLMConsolidationConfig
from synthorg.memory.consolidation.llm_op_prompts import (
    BASE_SYSTEM_PROMPT,
    fallback_summary,
)
from synthorg.memory.consolidation.provider_port import CompletionPort
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
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


class LLMSynthesisOp:
    """LLM synthesis op -- per-group LLM consolidation logic.

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
        provider: CompletionPort,
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

        Best-effort: degrades to empty context on non-system failure
        (logged WARNING), propagates ``MemoryError`` /
        ``RecursionError``.

        Returns:
            Result of type ``ConsolidationContext``.
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

        Synthesize and store the summary FIRST, then delete the
        originals, so a synthesis/store failure loses no data. Only
        entries the prompt cap admitted are eligible for deletion.

        Returns:
            Result of type ``OpResult``.
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
        """Retrieve recent distillation entries as disambiguation context.

        Returns:
            Tuple of ``MemoryEntry``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
            CancelledError: If the related operation fails.
        """
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
        except builtins.MemoryError, RecursionError:
            logger.error(
                LLM_STRATEGY_ERROR,
                agent_id=agent_id,
                reason="system_error_in_trajectory_fetch",
                error_type="system",
            )
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
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
        """Store the synthesized summary, tagged by synthesis outcome.

        Returns:
            Result of type ``NotBlankStr``.
        """
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
        """Best-effort delete of the consolidated originals, one per entry.

        Returns:
            List of ``NotBlankStr``.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
            CancelledError: If the related operation fails.
        """
        removed_ids: list[NotBlankStr] = []
        for entry in to_remove:
            try:
                await self._backend.delete(agent_id, entry.id)
            except builtins.MemoryError, RecursionError:
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
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
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
        """Build prompts, call the LLM, and fall back to concatenation.

        Returns:
            Tuple ``(str, SynthesisOutcome, tuple[MemoryEntry, ...])``.
        """
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
        fallback = fallback_summary(
            entries,
            truncate_length=self._config.fallback_truncate_length,
        )
        return fallback, SynthesisOutcome.CONCAT_FALLBACK, entries

    def _build_user_prompt(
        self,
        entries: tuple[MemoryEntry, ...],
        agent_id: NotBlankStr,
        category: MemoryCategory,
    ) -> tuple[str, tuple[MemoryEntry, ...]]:
        """Build the fenced user prompt, dropping entries past the char cap.

        Returns:
            Tuple ``(str, tuple[MemoryEntry, ...])``.
        """
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
        """Call the provider under a cost-recording scope; ``None`` on failure.

        Returns:
            The resulting ``str``, or ``None`` when unavailable.

        Raises:
            MemoryError: If the related operation fails.
            RecursionError: If the related operation fails.
            CancelledError: If the related operation fails.
        """
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
        except builtins.MemoryError, RecursionError:
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
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
        """Fall back on retryable provider errors; re-raise non-retryable.

        Returns:
            The resulting ``str``, or ``None`` when unavailable.

        Raises:
            exc: Raised when the relevant invariant fails.
        """
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
        log_exception_redacted(
            logger,
            LLM_STRATEGY_ERROR,
            exc,
            agent_id=agent_id,
            category=category.value,
            entry_count=entry_count,
            model=self._model,
            reason="non_retryable_provider_error",
        )
        raise exc

    def _build_system_prompt(
        self,
        trajectory_context: tuple[MemoryEntry, ...],
    ) -> str:
        """Build the system prompt, appending fenced trajectory context.

        Returns:
            Result of type ``str``.
        """
        if not trajectory_context:
            return BASE_SYSTEM_PROMPT
        context_lines = ["\nRecent trajectory context (for disambiguation only):"]
        for entry in trajectory_context:
            snippet = entry.content[: self._config.max_trajectory_chars_per_entry]
            context_lines.append(
                "- " + wrap_untrusted(TAG_MEMORY_ENTRY, snippet),
            )
        return BASE_SYSTEM_PROMPT + "\n" + "\n".join(context_lines)
