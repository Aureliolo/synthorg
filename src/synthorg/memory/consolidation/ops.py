"""Non-LLM consolidation ops for the axis split (ADR-0005).

Each op owns its backend and reproduces one pre-split strategy's
exact store + delete failure semantics:

- :class:`ConcatenationOp` -- Simple's truncated-bullet summary;
  delete result ignored, every original appended to ``removed_ids``
  (no ``try/except``, no bool check).
- :class:`DensityRoutingOp` -- DualMode's ``_process_group``:
  classify the *full* group (kept + to_remove) by majority vote,
  build content extractively or abstractively, store with the
  ``mode:<...>`` tag, delete with ``if not deleted: continue`` and
  emit one :class:`ArchivalModeAssignment` per deleted original.
- :class:`ExtractivePreservationOp` / :class:`AbstractiveSummarizationOp`
  -- standalone pluggable ops (new surface enabled by the split) that
  wrap the existing preserver / summarizer with the DualMode-lineage
  ``if not deleted: continue`` delete rule.

``LLMSynthesisOp`` lives in
:mod:`synthorg.memory.consolidation.llm_op` (its synthesis + prompt
+ fallback machinery is substantial).
"""

import asyncio
from typing import TYPE_CHECKING, Final

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.consolidation.axis import (
    ConsolidationContext,
    OpResult,
    SelectionGroup,
)
from synthorg.memory.consolidation.density import ContentDensity
from synthorg.memory.consolidation.models import (
    ArchivalMode,
    ArchivalModeAssignment,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata, MemoryStoreRequest
from synthorg.observability import get_logger
from synthorg.observability.events.consolidation import DUAL_MODE_GROUP_CLASSIFIED

if TYPE_CHECKING:
    from synthorg.memory.consolidation.abstractive import AbstractiveSummarizer
    from synthorg.memory.consolidation.density import DensityClassifier
    from synthorg.memory.consolidation.extractive import ExtractivePreserver
    from synthorg.memory.protocol import MemoryBackend

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
    """Simple strategy's operation: truncated-bullet concatenation.

    Store + delete semantics reproduce ``SimpleConsolidationStrategy``
    exactly: the summary is stored with the lone ``"consolidated"``
    tag, then every entry is deleted with the backend result ignored
    (no ``try/except``, no bool check) and unconditionally appended to
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
        """Reproduce ``SimpleConsolidationStrategy._build_summary``.

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
    """DualMode delete rule: ``if not deleted: continue``.

    Returns the successfully-deleted ids and (when ``mode`` is given)
    one :class:`ArchivalModeAssignment` per deleted original, matching
    ``DualModeConsolidationStrategy._process_group`` byte-for-byte.

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
    """DualMode's operation: density-routed extractive/abstractive.

    Reproduces ``DualModeConsolidationStrategy._process_group`` /
    ``_build_content`` / ``_determine_group_mode`` exactly: classify
    the *full* group (kept + to_remove) by majority vote (strict
    ``>`` so a 50/50 split is ABSTRACTIVE), build the consolidated
    content with the routed mode, store it tagged
    ``("consolidated", "mode:<mode>")``, then delete with the
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
        """Classify -> route -> store -> delete (DualMode semantics).

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
        """Reproduce ``DualModeConsolidationStrategy._build_content``.

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


class ExtractivePreservationOp(_PlainPrepareMixin):
    """Standalone extractive op (new pluggable surface).

    Stores the joined :class:`ExtractivePreserver` output tagged
    ``("consolidated", "mode:extractive")`` and deletes with the
    DualMode-lineage ``if not deleted: continue`` rule.

    Args:
        backend: Memory backend.
        extractor: Extractive preserver instance.
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        extractor: ExtractivePreserver,
    ) -> None:
        self._backend = backend
        self._extractor = extractor

    async def consolidate(
        self,
        group: SelectionGroup,
        *,
        context: ConsolidationContext,
    ) -> OpResult:
        """Extract + store + delete.

        Returns:
            Result of type ``OpResult``.
        """
        content = _DUAL_MODE_SEPARATOR.join(
            self._extractor.extract(e.content) for e in group.to_remove
        )
        store_request = MemoryStoreRequest(
            category=group.category,
            content=content,
            metadata=MemoryMetadata(
                source="consolidation",
                tags=("consolidated", f"mode:{ArchivalMode.EXTRACTIVE.value}"),
            ),
        )
        new_id = await self._backend.store(context.agent_id, store_request)
        removed_ids, assignments = await _delete_dual_mode(
            self._backend,
            context.agent_id,
            group.category,
            group.to_remove,
            mode=ArchivalMode.EXTRACTIVE,
        )
        return OpResult(
            summary_id=new_id,
            removed_ids=tuple(removed_ids),
            mode_assignments=tuple(assignments),
        )


class AbstractiveSummarizationOp(_PlainPrepareMixin):
    """Standalone abstractive op (new pluggable surface).

    Stores the joined :class:`AbstractiveSummarizer` output (parallel
    per-entry fan-out via ``asyncio.TaskGroup``) tagged
    ``("consolidated", "mode:abstractive")`` and deletes with the
    DualMode-lineage ``if not deleted: continue`` rule.

    Args:
        backend: Memory backend.
        summarizer: Abstractive summarizer instance.
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        summarizer: AbstractiveSummarizer,
    ) -> None:
        self._backend = backend
        self._summarizer = summarizer

    async def consolidate(
        self,
        group: SelectionGroup,
        *,
        context: ConsolidationContext,
    ) -> OpResult:
        """Summarise + store + delete.

        Returns:
            Result of type ``OpResult``.
        """
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(
                    self._summarizer.summarize(e.content, agent_id=e.agent_id)
                )
                for e in group.to_remove
            ]
        content = _DUAL_MODE_SEPARATOR.join(t.result() for t in tasks)
        store_request = MemoryStoreRequest(
            category=group.category,
            content=content,
            metadata=MemoryMetadata(
                source="consolidation",
                tags=("consolidated", f"mode:{ArchivalMode.ABSTRACTIVE.value}"),
            ),
        )
        new_id = await self._backend.store(context.agent_id, store_request)
        removed_ids, assignments = await _delete_dual_mode(
            self._backend,
            context.agent_id,
            group.category,
            group.to_remove,
            mode=ArchivalMode.ABSTRACTIVE,
        )
        return OpResult(
            summary_id=new_id,
            removed_ids=tuple(removed_ids),
            mode_assignments=tuple(assignments),
        )
