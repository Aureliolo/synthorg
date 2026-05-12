"""Dual-mode consolidation strategy.

Density-aware consolidation: classifies entries as sparse or dense,
then applies LLM abstractive summarization (sparse) or extractive
preservation (dense) accordingly.
"""

import asyncio
from itertools import groupby
from operator import attrgetter
from typing import Final

from synthorg.core.enums import MemoryCategory  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.consolidation.abstractive import (
    AbstractiveSummarizer,  # noqa: TC001
)
from synthorg.memory.consolidation.density import (
    ContentDensity,
    DensityClassifier,
)
from synthorg.memory.consolidation.extractive import (
    ExtractivePreserver,  # noqa: TC001
)
from synthorg.memory.consolidation.models import (
    ArchivalMode,
    ArchivalModeAssignment,
    ConsolidationResult,
)
from synthorg.memory.models import MemoryEntry, MemoryMetadata, MemoryStoreRequest
from synthorg.memory.protocol import MemoryBackend  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.consolidation import (
    DUAL_MODE_GROUP_CLASSIFIED,
    STRATEGY_COMPLETE,
    STRATEGY_START,
)

logger = get_logger(__name__)

_DEFAULT_GROUP_THRESHOLD: Final[int] = 3
_MIN_GROUP_THRESHOLD: Final[int] = 2


class DualModeConsolidationStrategy:
    """Density-aware consolidation strategy.

    Classifies entries by content density and applies the appropriate
    archival mode: LLM abstractive summarization for sparse content,
    extractive key-fact preservation for dense content.

    Groups entries by category.  For each group exceeding the threshold,
    classifies density per-entry, determines the group mode by majority
    vote, selects the best entry to keep, and processes the rest.

    Args:
        backend: Memory backend for storing summaries/extractions.
        classifier: Density classifier instance.
        extractor: Extractive preserver instance.
        summarizer: Abstractive summarizer instance.
        group_threshold: Minimum group size to trigger consolidation
            (must be >= 2).

    Raises:
        ValueError: If ``group_threshold`` is less than 2.
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        classifier: DensityClassifier,
        extractor: ExtractivePreserver,
        summarizer: AbstractiveSummarizer,
        group_threshold: int = _DEFAULT_GROUP_THRESHOLD,
    ) -> None:
        if group_threshold < _MIN_GROUP_THRESHOLD:
            msg = (
                f"group_threshold must be >= {_MIN_GROUP_THRESHOLD}, "
                f"got {group_threshold}"
            )
            raise ValueError(msg)
        self._backend = backend
        self._classifier = classifier
        self._extractor = extractor
        self._summarizer = summarizer
        self._group_threshold = group_threshold

    async def consolidate(
        self,
        entries: tuple[MemoryEntry, ...],
        *,
        agent_id: NotBlankStr,
    ) -> ConsolidationResult:
        """Consolidate entries using density-aware dual-mode approach.

        Groups entries by category, classifies density, selects archival
        mode by majority vote, then processes entries accordingly.

        Args:
            entries: Memory entries to consolidate.
            agent_id: Owning agent identifier.

        Returns:
            Result describing what was consolidated.
        """
        if not entries:
            logger.debug(
                STRATEGY_COMPLETE,
                agent_id=agent_id,
                consolidated_count=0,
                strategy="dual_mode",
            )
            return ConsolidationResult()

        logger.info(
            STRATEGY_START,
            agent_id=agent_id,
            entry_count=len(entries),
            strategy="dual_mode",
        )

        removed_ids: list[NotBlankStr] = []
        summary_ids: list[NotBlankStr] = []
        mode_assignments: list[ArchivalModeAssignment] = []

        sorted_entries = sorted(entries, key=attrgetter("category"))
        groups = groupby(sorted_entries, key=attrgetter("category"))

        for category, group_iter in groups:
            group = list(group_iter)
            if len(group) < self._group_threshold:
                continue
            new_id, group_removed, group_modes = await self._process_group(
                category,
                group,
                agent_id,
            )
            summary_ids.append(new_id)
            removed_ids.extend(group_removed)
            mode_assignments.extend(group_modes)

        result = ConsolidationResult(
            removed_ids=tuple(removed_ids),
            summary_ids=tuple(summary_ids),
            mode_assignments=tuple(mode_assignments),
        )

        logger.info(
            STRATEGY_COMPLETE,
            agent_id=agent_id,
            consolidated_count=result.consolidated_count,
            summary_id=result.summary_id,
            strategy="dual_mode",
        )

        return result

    async def _process_group(
        self,
        category: MemoryCategory,
        group: list[MemoryEntry],
        agent_id: NotBlankStr,
    ) -> tuple[
        NotBlankStr,
        list[NotBlankStr],
        list[ArchivalModeAssignment],
    ]:
        """Process a single category group for consolidation.

        Args:
            category: The memory category.
            group: Entries in this category.
            agent_id: Owning agent identifier.

        Returns:
            Tuple of (summary_id, removed_ids, mode_assignments).
        """
        group_tuple = tuple(group)
        group_mode = self._determine_group_mode(group_tuple)
        _, to_remove = self._select_entries(group_tuple)

        logger.debug(
            DUAL_MODE_GROUP_CLASSIFIED,
            agent_id=agent_id,
            category=category.value,
            group_size=len(group),
            mode=group_mode.value,
        )

        content = await self._build_content(to_remove, group_mode)
        store_request = MemoryStoreRequest(
            category=category,
            content=content,
            metadata=MemoryMetadata(
                source="consolidation",
                tags=("consolidated", f"mode:{group_mode.value}"),
            ),
        )
        new_id = await self._backend.store(agent_id, store_request)

        removed_ids: list[NotBlankStr] = []
        assignments: list[ArchivalModeAssignment] = []
        for entry in to_remove:
            deleted = await self._backend.delete(agent_id, entry.id)
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
            assignments.append(
                ArchivalModeAssignment(
                    original_id=entry.id,
                    mode=group_mode,
                ),
            )
        return new_id, removed_ids, assignments

    def _determine_group_mode(
        self,
        group: tuple[MemoryEntry, ...],
    ) -> ArchivalMode:
        """Determine archival mode for a group by majority vote.

        Args:
            group: Entries in the same category.

        Returns:
            EXTRACTIVE if majority is dense, ABSTRACTIVE otherwise.
        """
        classified = self._classifier.classify_batch(group)
        dense_count = sum(
            1 for _, density in classified if density == ContentDensity.DENSE
        )
        # Tie-breaking: 50/50 dense/sparse splits default to
        # ABSTRACTIVE (strict > comparison), which is the safer mode.
        is_majority_dense = dense_count > len(classified) / 2
        return (
            ArchivalMode.EXTRACTIVE if is_majority_dense else ArchivalMode.ABSTRACTIVE
        )

    def _select_entries(
        self,
        group: tuple[MemoryEntry, ...],
    ) -> tuple[MemoryEntry, tuple[MemoryEntry, ...]]:
        """Select the best entry to keep and the rest to remove.

        Entries with ``None`` relevance scores are treated as ``0.0``.
        When scores are equal, the most recently created entry wins.

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

    async def _build_content(
        self,
        entries: tuple[MemoryEntry, ...],
        mode: ArchivalMode,
    ) -> str:
        """Build consolidated content using the appropriate mode.

        Args:
            entries: Entries being consolidated.
            mode: Archival mode to apply.

        Returns:
            Consolidated content text.
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
        return "\n---\n".join(parts)
