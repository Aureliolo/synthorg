"""LLM-based memory consolidation strategy.

Feeds related memories (grouped by category) to an LLM for semantic
deduplication and synthesis.  When distillation entries (tagged
``"distillation"`` by ``capture_distillation``) exist for the agent,
their trajectory summaries and outcomes are included in the synthesis
system prompt as context.

Falls back to simple concatenation when the LLM call fails with a
retryable error (after retries are exhausted) or returns empty content.
"""

import asyncio
from enum import StrEnum
from itertools import groupby
from operator import attrgetter

from synthorg.budget.call_category import LLMCallCategory

# ``CostTracker`` is part of ``LLMConsolidationStrategy.__init__``'s
# public annotation, so it must resolve at runtime when downstream
# tooling evaluates type hints (DI containers, doc generators).
from synthorg.budget.tracker import CostTracker  # noqa: TC001
from synthorg.core.enums import MemoryCategory  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_MEMORY_ENTRY,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.memory.consolidation.config import LLMConsolidationConfig
from synthorg.memory.consolidation.models import ConsolidationResult
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.memory.protocol import MemoryBackend  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.consolidation import (
    LLM_STRATEGY_ERROR,
    LLM_STRATEGY_FALLBACK,
    LLM_STRATEGY_SYNTHESIZED,
    STRATEGY_COMPLETE,
    STRATEGY_START,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import ProviderError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider  # noqa: TC001
from synthorg.providers.resilience.errors import RetryExhaustedError

logger = get_logger(__name__)


#: Tag read from the backend to locate distillation entries produced
#: by ``synthorg.memory.consolidation.distillation.capture_distillation``.
#: Kept as a literal here to avoid a cross-module import that would
#: pull the engine execution protocol into the consolidation strategy
#: module unnecessarily.
_DISTILLATION_TAG: NotBlankStr = "distillation"

#: Tag applied to LLM-produced summaries.  Used to distinguish them
#: from the concatenation fallback (tagged with ``_CONCAT_FALLBACK_TAG``).
_LLM_SYNTHESIZED_TAG: NotBlankStr = "llm-synthesized"

#: Tag applied to concatenation-fallback summaries.
_CONCAT_FALLBACK_TAG: NotBlankStr = "concat-fallback"


class SynthesisOutcome(StrEnum):
    """Outcome of an LLM synthesis attempt.

    Replaces the bare ``bool`` that ``_synthesize`` previously returned
    as its second element, making intent explicit.
    """

    LLM_SYNTHESIZED = "llm_synthesized"
    """LLM returned non-empty content -- real synthesis occurred."""

    CONCAT_FALLBACK = "concat_fallback"
    """LLM call failed or returned empty -- concatenation fallback used."""


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


class LLMConsolidationStrategy:
    """LLM-based memory consolidation strategy.

    Groups entries by category.  For each group exceeding the threshold,
    keeps the entry with the highest relevance score (with most recent
    as tiebreaker).  The kept entry is NOT included in the LLM
    synthesis input -- it remains in the backend unchanged, while the
    remaining entries are fed to the LLM, the synthesized summary is
    stored, and the originals are deleted.

    Category groups are processed in parallel via ``asyncio.TaskGroup``.

    When an agent has distillation entries (memory entries tagged
    ``"distillation"`` by ``capture_distillation``) present in the
    backend, a best-effort lookup fetches the most recent ones and
    includes their trajectory summaries and outcomes in the synthesis
    system prompt as trajectory context.  Lookup failures degrade
    gracefully (logged at WARNING, plain synthesis without trajectory).

    Falls back to simple concatenation when ``provider.complete``
    raises ``RetryExhaustedError`` (all retries consumed) or returns
    an empty/whitespace response.  Non-retryable ``ProviderError``
    subclasses propagate to the caller (logged at ERROR first).
    Unexpected non-provider exceptions also fall back to concatenation
    (logged at WARNING with full traceback).

    Args:
        backend: Memory backend for storing summaries and reading
            distillation entries.
        provider: Completion provider for LLM synthesis calls.
        model: Model identifier for the synthesis LLM.
        config: LLM consolidation configuration.  All tuning knobs
            (thresholds, token limits, distillation context toggles)
            are encapsulated here.
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

    async def consolidate(
        self,
        entries: tuple[MemoryEntry, ...],
        *,
        agent_id: NotBlankStr,
    ) -> ConsolidationResult:
        """Consolidate entries using LLM-based semantic synthesis.

        Groups entries by category, fetches distillation trajectory
        context (when enabled), and processes groups in parallel.
        For each group exceeding ``group_threshold``, selects the
        best entry to keep, synthesizes the rest via LLM with optional
        trajectory context, stores the summary, and deletes the
        consolidated entries.

        ``ConsolidationResult.summary_ids`` contains every summary
        created during the run (one per processed group); the
        backward-compatible ``summary_id`` accessor returns the last
        element for callers that only need a representative id.

        Args:
            entries: Memory entries to consolidate.
            agent_id: Owning agent identifier.

        Returns:
            Result describing what was consolidated.
        """
        if not entries:
            return ConsolidationResult()

        logger.info(
            STRATEGY_START,
            agent_id=agent_id,
            entry_count=len(entries),
            strategy="llm",
        )

        # Build groups BEFORE any backend calls so a below-threshold
        # batch short-circuits without hitting the distillation
        # lookup.  Otherwise a batch with nothing to consolidate would
        # still pay a round-trip and could trip the trajectory-fetch
        # degradation logger on what should be a pure no-op.
        groups_to_process = self._build_groups(entries)
        if not groups_to_process:
            logger.info(
                STRATEGY_COMPLETE,
                agent_id=agent_id,
                consolidated_count=0,
                summary_count=0,
                strategy="llm",
            )
            return ConsolidationResult()

        trajectory_context = await self._fetch_trajectory_context(agent_id)
        group_results = await self._run_groups(
            groups_to_process,
            agent_id,
            trajectory_context,
        )
        result = self._assemble_result(group_results)

        logger.info(
            STRATEGY_COMPLETE,
            agent_id=agent_id,
            consolidated_count=result.consolidated_count,
            summary_count=len(result.summary_ids),
            strategy="llm",
        )
        return result

    def _build_groups(
        self,
        entries: tuple[MemoryEntry, ...],
    ) -> list[tuple[MemoryCategory, list[MemoryEntry]]]:
        """Group entries by category, keeping only groups >= threshold."""
        groups: list[tuple[MemoryCategory, list[MemoryEntry]]] = []
        sorted_entries = sorted(entries, key=attrgetter("category"))
        for category, group_iter in groupby(sorted_entries, key=attrgetter("category")):
            group = list(group_iter)
            if len(group) >= self._config.group_threshold:
                groups.append((category, group))
        return groups

    async def _run_groups(
        self,
        groups_to_process: list[tuple[MemoryCategory, list[MemoryEntry]]],
        agent_id: NotBlankStr,
        trajectory_context: tuple[MemoryEntry, ...],
    ) -> list[tuple[NotBlankStr, list[NotBlankStr]]]:
        """Run ``_process_group`` for each group concurrently.

        Unwraps ``ExceptionGroup`` produced by ``asyncio.TaskGroup`` so
        callers see the original exception type (matching sequential
        semantics).  Every ``except*`` branch logs the full exception
        count before re-raising so operators can diagnose multi-task
        failures even though only the first exception surfaces.
        """
        if not groups_to_process:
            return []
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [
                    tg.create_task(
                        self._process_group(
                            category,
                            group,
                            agent_id,
                            trajectory_context,
                        )
                    )
                    for category, group in groups_to_process
                ]
        except* MemoryError as eg:
            self._log_taskgroup_failure(
                agent_id, eg, "task_group_memory_error", severity="error"
            )
            raise eg.exceptions[0] from eg
        except* RecursionError as eg:
            self._log_taskgroup_failure(
                agent_id, eg, "task_group_recursion_error", severity="error"
            )
            raise eg.exceptions[0] from eg
        except* ProviderError as eg:
            self._log_taskgroup_failure(
                agent_id, eg, "task_group_provider_error", severity="error"
            )
            raise eg.exceptions[0] from eg
        except* Exception as eg:
            self._log_taskgroup_failure(
                agent_id, eg, "task_group_unexpected_error", severity="error"
            )
            raise eg.exceptions[0] from eg
        return [task.result() for task in tasks]

    @staticmethod
    def _log_taskgroup_failure(
        agent_id: NotBlankStr,
        eg: BaseExceptionGroup[BaseException],
        reason: str,
        *,
        severity: str,
    ) -> None:
        """Log a TaskGroup failure, preserving sibling exception info."""
        log_fn = logger.error if severity == "error" else logger.warning
        log_fn(
            LLM_STRATEGY_ERROR,
            agent_id=agent_id,
            reason=reason,
            exception_count=len(eg.exceptions),
            exception_types=[type(e).__name__ for e in eg.exceptions],
        )

    @staticmethod
    def _assemble_result(
        group_results: list[tuple[NotBlankStr, list[NotBlankStr]]],
    ) -> ConsolidationResult:
        """Combine per-group results into a single ``ConsolidationResult``."""
        removed_ids: list[NotBlankStr] = []
        summary_ids: list[NotBlankStr] = []
        for new_id, group_removed in group_results:
            summary_ids.append(new_id)
            removed_ids.extend(group_removed)
        return ConsolidationResult(
            removed_ids=tuple(removed_ids),
            summary_ids=tuple(summary_ids),
        )

    async def _fetch_trajectory_context(
        self,
        agent_id: NotBlankStr,
    ) -> tuple[MemoryEntry, ...]:
        """Fetch recent distillation entries as trajectory context.

        Best-effort: non-system failures degrade to empty context (no
        trajectory information included in the synthesis prompt) and
        are logged at WARNING so operators can observe the
        degradation.  System errors (``MemoryError``, ``RecursionError``)
        propagate.  Returns at most ``config.max_trajectory_context_entries``
        entries.
        """
        if not self._config.include_distillation_context:
            return ()
        try:
            # Backend.retrieve is relevance-ordered by contract; sort
            # locally by created_at descending and slice to the N most
            # recent entries so the synthesis prompt sees the latest
            # trajectory context regardless of backend ordering.
            query = MemoryQuery(
                tags=(_DISTILLATION_TAG,),
                limit=self._config.max_trajectory_context_entries * 4,
            )
            raw = await self._backend.retrieve(agent_id, query)
            by_recency = sorted(
                raw,
                key=attrgetter("created_at"),
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

    async def _process_group(
        self,
        category: MemoryCategory,
        group: list[MemoryEntry],
        agent_id: NotBlankStr,
        trajectory_context: tuple[MemoryEntry, ...],
    ) -> tuple[NotBlankStr, list[NotBlankStr]]:
        """Process a single category group for consolidation.

        Synthesizes and stores the summary FIRST, then deletes the
        originals.  This ordering prevents data loss: if synthesis or
        the store call fails (including non-retryable ProviderError),
        no originals are deleted and the caller sees the exception
        without losing any data.

        When ``_build_user_prompt`` truncates the input (total char cap
        reached), only the entries that were actually summarized are
        eligible for deletion -- dropped entries remain in the backend
        so their facts are not lost on the next consolidation pass.

        If the store succeeds but some individual deletes fail, the
        affected originals remain alongside the summary (duplicated
        data, recoverable on the next consolidation pass).

        Returns:
            Tuple of (summary_id, removed_ids).
        """
        _, to_remove = self._select_entries(group)
        synthesized, outcome, summarized = await self._synthesize(
            to_remove,
            agent_id=agent_id,
            category=category,
            trajectory_context=trajectory_context,
        )
        new_id = await self._store_summary(
            synthesized,
            category=category,
            agent_id=agent_id,
            outcome=outcome,
        )
        if outcome == SynthesisOutcome.LLM_SYNTHESIZED:
            logger.info(
                LLM_STRATEGY_SYNTHESIZED,
                agent_id=agent_id,
                category=category.value,
                entry_count=len(summarized),
                summary_id=new_id,
                model=self._model,
                trajectory_context_count=len(trajectory_context),
            )
        removed_ids = await self._delete_consolidated(
            summarized,
            agent_id=agent_id,
            category=category,
        )
        return new_id, removed_ids

    async def _store_summary(
        self,
        content: str,
        *,
        category: MemoryCategory,
        agent_id: NotBlankStr,
        outcome: SynthesisOutcome,
    ) -> NotBlankStr:
        """Store the synthesized summary and return the new entry id."""
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
        """Best-effort delete of originals after the summary is stored.

        Individual delete failures are tolerated: the loop continues,
        logs the failure, and only successfully-deleted entry IDs are
        returned in ``removed_ids``.  System errors propagate.
        """
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
        """Synthesize multiple entries into a single summary via LLM.

        The per-entry content is truncated to ``config.max_entry_input_chars``
        before being sent to the LLM, and the total concatenated user
        content is capped at ``config.max_total_user_content_chars`` to guard
        against oversized groups that would blow out context windows or
        cost budgets.  When ``trajectory_context`` is non-empty,
        distillation entry trajectories are included in the system
        prompt.

        Returns a ``(summary, outcome, summarized_entries)`` triple:

        - ``summary`` is the text to store on the backend.
        - ``outcome`` indicates whether the LLM produced the summary
          or a concatenation fallback was used.
        - ``summarized_entries`` is the subset of ``entries`` that was
          actually represented in the summary.  When the user prompt is
          truncated at ``config.max_total_user_content_chars``, dropped
          entries are NOT in this list and the caller MUST NOT delete
          them (they remain on the backend for the next consolidation
          pass).

        Fallback paths (return with ``CONCAT_FALLBACK``):

        - ``RetryExhaustedError`` (all retries exhausted)
        - Retryable ``ProviderError`` surfaced directly (tests,
          edge configurations that bypass the retry handler)
        - Empty or whitespace-only LLM response
        - Unexpected non-``ProviderError`` exception (logged WARNING
          with full traceback)

        Non-retryable ``ProviderError`` subclasses are logged at ERROR
        and propagated to the caller.

        Args:
            entries: Entries to synthesize.
            agent_id: Owning agent for log context.
            category: Memory category for log context.
            trajectory_context: Distillation entries to include as
                context (may be empty).

        Returns:
            ``(summary, outcome, summarized_entries)`` triple.
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
        # Fallback path: concatenate every input entry (no truncation
        # tradeoffs on the concat path -- a terse per-entry summary is
        # safe even for oversized groups), and allow the caller to
        # delete all of them since each one is represented in the
        # concatenation summary.
        fallback = self._fallback_summary(entries)
        return fallback, SynthesisOutcome.CONCAT_FALLBACK, entries

    def _build_user_prompt(
        self,
        entries: tuple[MemoryEntry, ...],
        agent_id: NotBlankStr,
        category: MemoryCategory,
    ) -> tuple[str, tuple[MemoryEntry, ...]]:
        """Build the user prompt with untrusted-content fences.

        Each entry is wrapped via :func:`wrap_untrusted` under the
        ``TAG_MEMORY_ENTRY`` tag so the consolidator LLM treats the
        content as data, and so any literal closing-tag breakout
        attempt inside the entry is rewritten before serialisation.

        The entry's ``category`` is rendered as a plain-text
        ``"category: <value>"`` line inside the fenced body, NOT as
        an XML attribute on the opening tag. Attribute-style
        rendering was the original failure mode of the hand-rolled
        XML escape: an attacker who controlled the category value
        could break out of the attribute quoting. As a plain line
        inside the fence, the category is normal data;
        ``wrap_untrusted`` already protects against closing-tag
        injection so the only exit from the fence is the wrapper's
        own trailing ``</memory-entry>``.

        The total concatenated length is capped at
        ``config.max_total_user_content_chars``; if the cap is reached,
        remaining entries are dropped, the truncation is logged, and
        they are omitted from the returned ``included`` tuple so the
        caller can avoid deleting memories that were never summarized.

        Returns:
            ``(prompt_text, included_entries)`` -- the second element
            contains the entries that actually made it into the prompt,
            in prompt order.
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
            total_chars += len(piece) + 1  # +1 for the joining newline
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
        """Call the LLM and return stripped content, or ``None`` on fallback.

        Returns ``None`` for every fallback path (retry exhausted,
        retryable provider error, empty response, unexpected
        exception).  Propagates non-retryable ``ProviderError`` (after
        logging at ERROR) and system errors.
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
        """Classify a ``ProviderError``: fallback for retryable, raise otherwise.

        Returns ``None`` to signal fallback for retryable errors.
        Logs non-retryable errors at ERROR (with full context) and
        re-raises.
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
        """Build the synthesis system prompt with optional trajectory context.

        Trajectory snippets are upstream agent memory by another name,
        so we wrap them under the same ``TAG_MEMORY_ENTRY`` fence as
        the consolidation entries themselves; this keeps the
        ``untrusted_content_directive`` listing short (one tag) and
        consistent with the user-prompt path.
        """
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
        """Build a simple concatenation summary as fallback.

        Returns an empty string when ``entries`` is empty so that the
        caller (which still tags the stored record as
        ``concat-fallback``) is not forced to special-case this path.
        """
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

    def _select_entries(
        self,
        group: list[MemoryEntry],
    ) -> tuple[MemoryEntry, tuple[MemoryEntry, ...]]:
        """Select the best entry to keep and the rest to remove.

        Entries with ``None`` relevance scores are treated as ``0.0``
        for comparison.  When scores are equal, the most recently
        created entry wins.

        Args:
            group: Entries in the same category.

        Returns:
            Tuple of (kept entry, entries to remove).
        """
        best = max(
            group,
            key=lambda e: (
                e.relevance_score if e.relevance_score is not None else 0.0,
                e.created_at,
            ),
        )
        to_remove = tuple(e for e in group if e.id != best.id)
        return best, to_remove
