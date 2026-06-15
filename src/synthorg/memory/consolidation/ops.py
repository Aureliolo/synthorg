"""Non-LLM consolidation ops for the axis split (ADR-0005).

Each op owns its backend; the three differ deliberately in their
store + delete failure semantics:

- :class:`ConcatenationOp` -- truncated-bullet summary; delete result
  ignored, every original appended to ``removed_ids`` (no
  ``try/except``, no bool check).
- :class:`DensityRoutingOp` -- classify the *full* group
  (kept + to_remove) by majority vote, build content extractively or
  abstractively, store with the ``mode:<...>`` tag, delete with
  ``if not deleted: continue`` and emit one
  :class:`ArchivalModeAssignment` per deleted original.
- :class:`SingleModeOp` -- one archival-mode op parameterised by a
  content builder, bound to the extractive or abstractive mode via
  :func:`extractive_preservation_op` / :func:`abstractive_summarization_op`,
  applying the ``if not deleted: continue`` delete rule.

``LLMSynthesisOp`` lives in
:mod:`synthorg.memory.consolidation.llm_op` (its synthesis + prompt
+ fallback machinery is substantial).
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Final

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.consolidation.abstractive import AbstractiveSummarizer
from synthorg.memory.consolidation.axis import (
    ConsolidationContext,
    OpResult,
    SelectionGroup,
)
from synthorg.memory.consolidation.density import ContentDensity, DensityClassifier
from synthorg.memory.consolidation.extractive import ExtractivePreserver
from synthorg.memory.consolidation.models import (
    ArchivalMode,
    ArchivalModeAssignment,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata, MemoryStoreRequest
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger
from synthorg.observability.events.consolidation import DUAL_MODE_GROUP_CLASSIFIED

logger = get_logger(__name__)

_SUMMARY_TRUNCATE_LENGTH: Final[int] = 200
_DUAL_MODE_SEPARATOR: Final[str] = "\n---\n"


class _PlainPrepareMixin:
    """Default ``prepare``: a context with no trajectory entries.

    Every op except ``LLMSynthesisOp`` (which fetches distillation
    trajectory once per run) shares this.
    """

    async def prepare(
        self,
        agent_id: NotBlankStr,
    ) -> ConsolidationContext:
        """Return a trajectory-free per-run context.

        Returns:
            Result of type ``ConsolidationContext``.
        """
        return ConsolidationContext(agent_id=agent_id)


class ConcatenationOp(_PlainPrepareMixin):
    """Truncated-bullet concatenation op.

    The summary is stored with the lone ``"consolidated"`` tag, then
    every entry is deleted with the backend result ignored (no
    ``try/except``, no bool check) and unconditionally appended to
    ``removed_ids``.

    Args:
        backend: Memory backend for storing the summary + deleting
            originals.
    """

    def __init__(self, *, backend: MemoryBackend) -> None:
        self._backend = backend

    async def consolidate(
        self,
        group: SelectionGroup,
        *,
        context: ConsolidationContext,
    ) -> OpResult:
        """Build, store, and delete -- Simple's exact semantics.

        Returns:
            Result of type ``OpResult``.
        """
        summary = self._build_summary(group.category, group.to_remove)
        store_request = MemoryStoreRequest(
            category=group.category,
            content=summary,
            metadata=MemoryMetadata(
                source="consolidation",
                tags=("consolidated",),
            ),
        )
        new_id = await self._backend.store(context.agent_id, store_request)
        removed_ids: list[NotBlankStr] = []
        for entry in group.to_remove:
            await self._backend.delete(context.agent_id, entry.id)
            removed_ids.append(entry.id)
        return OpResult(summary_id=new_id, removed_ids=tuple(removed_ids))

    @staticmethod
    def _build_summary(
        category: MemoryCategory,
        entries: tuple[MemoryEntry, ...],
    ) -> str:
        """Build a truncated-bullet summary for *entries*.

        Returns:
            Result of type ``str``.
        """
        lines = [f"Consolidated {category.value} memories:"]
        for entry in entries:
            truncated = (
                entry.content[:_SUMMARY_TRUNCATE_LENGTH] + "..."
                if len(entry.content) > _SUMMARY_TRUNCATE_LENGTH
                else entry.content
            )
            lines.append(f"- {truncated}")
        return "\n".join(lines)


async def _delete_dual_mode(
    backend: MemoryBackend,
    agent_id: NotBlankStr,
    category: MemoryCategory,
    to_remove: tuple[MemoryEntry, ...],
    *,
    mode: ArchivalMode | None,
) -> tuple[list[NotBlankStr], list[ArchivalModeAssignment]]:
    """Density-routing delete rule: ``if not deleted: continue``.

    Returns the successfully-deleted ids and (when ``mode`` is given)
    one :class:`ArchivalModeAssignment` per deleted original; an entry
    whose delete returns ``False`` is skipped, not recorded.

    Returns:
        Tuple ``(list[NotBlankStr], list[ArchivalModeAssignment])``.
    """
    removed_ids: list[NotBlankStr] = []
    assignments: list[ArchivalModeAssignment] = []
    for entry in to_remove:
        deleted = await backend.delete(agent_id, entry.id)
        if not deleted:
            logger.warning(
                DUAL_MODE_GROUP_CLASSIFIED,
                agent_id=agent_id,
                category=category.value,
                reason="delete_not_found",
                entry_id=entry.id,
            )
            continue
        removed_ids.append(entry.id)
        if mode is not None:
            assignments.append(
                ArchivalModeAssignment(original_id=entry.id, mode=mode),
            )
    return removed_ids, assignments


class DensityRoutingOp(_PlainPrepareMixin):
    """Density-routed extractive/abstractive consolidation op.

    Classifies the *full* group (kept + to_remove) by majority vote
    (strict ``>`` so a 50/50 split is ABSTRACTIVE), builds the
    consolidated content with the routed mode, stores it tagged
    ``("consolidated", "mode:<mode>")``, then deletes with the
    ``if not deleted: continue`` rule.

    Args:
        backend: Memory backend.
        classifier: Density classifier instance.
        extractor: Extractive preserver instance.
        summarizer: Abstractive summarizer instance.
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        classifier: DensityClassifier,
        extractor: ExtractivePreserver,
        summarizer: AbstractiveSummarizer,
    ) -> None:
        self._backend = backend
        self._classifier = classifier
        self._extractor = extractor
        self._summarizer = summarizer

    async def consolidate(
        self,
        group: SelectionGroup,
        *,
        context: ConsolidationContext,
    ) -> OpResult:
        """Classify -> route -> store -> delete one group.

        Returns:
            Result of type ``OpResult``.
        """
        full_group = (group.kept, *group.to_remove)
        group_mode = self._determine_group_mode(full_group)

        logger.debug(
            DUAL_MODE_GROUP_CLASSIFIED,
            agent_id=context.agent_id,
            category=group.category.value,
            group_size=len(full_group),
            mode=group_mode.value,
        )

        content = await self._build_content(group.to_remove, group_mode)
        store_request = MemoryStoreRequest(
            category=group.category,
            content=content,
            metadata=MemoryMetadata(
                source="consolidation",
                tags=("consolidated", f"mode:{group_mode.value}"),
            ),
        )
        new_id = await self._backend.store(context.agent_id, store_request)
        removed_ids, assignments = await _delete_dual_mode(
            self._backend,
            context.agent_id,
            group.category,
            group.to_remove,
            mode=group_mode,
        )
        return OpResult(
            summary_id=new_id,
            removed_ids=tuple(removed_ids),
            mode_assignments=tuple(assignments),
        )

    def _determine_group_mode(
        self,
        group: tuple[MemoryEntry, ...],
    ) -> ArchivalMode:
        """Majority-vote density (strict ``>``; tie -> ABSTRACTIVE).

        Returns:
            Result of type ``ArchivalMode``.
        """
        classified = self._classifier.classify_batch(group)
        dense_count = sum(
            1 for _, density in classified if density == ContentDensity.DENSE
        )
        is_majority_dense = dense_count > len(classified) / 2
        return (
            ArchivalMode.EXTRACTIVE if is_majority_dense else ArchivalMode.ABSTRACTIVE
        )

    async def _build_content(
        self,
        entries: tuple[MemoryEntry, ...],
        mode: ArchivalMode,
    ) -> str:
        """Build consolidated content for *entries* under the given mode.

        Returns:
            Result of type ``str``.
        """
        if mode == ArchivalMode.EXTRACTIVE:
            parts = [self._extractor.extract(e.content) for e in entries]
        else:
            async with asyncio.TaskGroup() as tg:
                tasks = [
                    tg.create_task(
                        self._summarizer.summarize(
                            e.content,
                            agent_id=e.agent_id,
                        )
                    )
                    for e in entries
                ]
            parts = [t.result() for t in tasks]
        return _DUAL_MODE_SEPARATOR.join(parts)


class SingleModeOp(_PlainPrepareMixin):
    """One archival-mode consolidation op parameterised by content builder.

    The store(tag ``mode:<mode>``) / ``_delete_dual_mode(mode=...)``
    pipeline lives once; the mode-specific content builder (*produce*)
    is data. The extractive and abstractive ops are two bindings of
    this class via :func:`extractive_preservation_op` /
    :func:`abstractive_summarization_op`.

    Args:
        backend: Memory backend.
        mode: Archival mode stamped on the stored tag and the dual-mode
            delete bookkeeping.
        produce: Mode-specific content builder mapping the removed
            entries to the joined summary content.
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        mode: ArchivalMode,
        produce: Callable[[Sequence[MemoryEntry]], Awaitable[str]],
    ) -> None:
        self._backend = backend
        self._mode = mode
        self._produce = produce

    async def consolidate(
        self,
        group: SelectionGroup,
        *,
        context: ConsolidationContext,
    ) -> OpResult:
        """Build content + store + delete.

        Returns:
            Result of type ``OpResult``.
        """
        content = await self._produce(group.to_remove)
        store_request = MemoryStoreRequest(
            category=group.category,
            content=content,
            metadata=MemoryMetadata(
                source="consolidation",
                tags=("consolidated", f"mode:{self._mode.value}"),
            ),
        )
        new_id = await self._backend.store(context.agent_id, store_request)
        removed_ids, assignments = await _delete_dual_mode(
            self._backend,
            context.agent_id,
            group.category,
            group.to_remove,
            mode=self._mode,
        )
        return OpResult(
            summary_id=new_id,
            removed_ids=tuple(removed_ids),
            mode_assignments=tuple(assignments),
        )


def extractive_preservation_op(
    *,
    backend: MemoryBackend,
    extractor: ExtractivePreserver,
) -> SingleModeOp:
    """Build the extractive-preservation op (synchronous extract per entry).

    Returns:
        A :class:`SingleModeOp` bound to ``EXTRACTIVE`` mode.
    """

    async def produce(entries: Sequence[MemoryEntry]) -> str:
        return _DUAL_MODE_SEPARATOR.join(extractor.extract(e.content) for e in entries)

    return SingleModeOp(
        backend=backend,
        mode=ArchivalMode.EXTRACTIVE,
        produce=produce,
    )


def abstractive_summarization_op(
    *,
    backend: MemoryBackend,
    summarizer: AbstractiveSummarizer,
) -> SingleModeOp:
    """Build the abstractive-summarization op (TaskGroup fan-out of summarize).

    Returns:
        A :class:`SingleModeOp` bound to ``ABSTRACTIVE`` mode.
    """

    async def produce(entries: Sequence[MemoryEntry]) -> str:
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(summarizer.summarize(e.content, agent_id=e.agent_id))
                for e in entries
            ]
        return _DUAL_MODE_SEPARATOR.join(t.result() for t in tasks)

    return SingleModeOp(
        backend=backend,
        mode=ArchivalMode.ABSTRACTIVE,
        produce=produce,
    )
