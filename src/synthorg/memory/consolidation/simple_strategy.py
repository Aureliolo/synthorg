"""Simple consolidation strategy.

Groups entries by category, keeps the most relevant entry per group
(with most recent as tiebreaker), and creates a summary entry from
the rest.
"""

from itertools import groupby
from operator import attrgetter

from synthorg.core.enums import MemoryCategory  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.consolidation.models import ConsolidationResult
from synthorg.memory.models import MemoryEntry, MemoryMetadata, MemoryStoreRequest
from synthorg.memory.protocol import MemoryBackend  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.consolidation import (
    STRATEGY_COMPLETE,
    STRATEGY_START,
)

logger = get_logger(__name__)

_SUMMARY_TRUNCATE_LENGTH = 200

_DEFAULT_GROUP_THRESHOLD = 3

_MIN_GROUP_THRESHOLD = 2


class SimpleConsolidationStrategy:
    """Simple memory consolidation strategy.

    Groups entries by category.  For each group exceeding a threshold,
    keeps the entry with the highest relevance score (with most recent
    as tiebreaker), creates a summary entry from the rest, and deletes
    consolidated entries from the backend.

    Args:
        backend: Memory backend for storing summaries.
        group_threshold: Minimum group size to trigger consolidation
            (must be >= 2).

    Raises:
        ValueError: If ``group_threshold`` is less than 2.
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        group_threshold: int = _DEFAULT_GROUP_THRESHOLD,
    ) -> None:
        if group_threshold < _MIN_GROUP_THRESHOLD:
            msg = (
                f"group_threshold must be >= {_MIN_GROUP_THRESHOLD}, "
                f"got {group_threshold}"
            )
            raise ValueError(msg)
        self._backend = backend
        self._group_threshold = group_threshold

    async def consolidate(
        self,
        entries: tuple[MemoryEntry, ...],
        *,
        agent_id: NotBlankStr,
    ) -> ConsolidationResult:
        """Consolidate entries by grouping and summarizing per category.

        Groups with fewer than ``group_threshold`` entries are left
        unchanged.

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
        )

        removed_ids: list[NotBlankStr] = []
        summary_id: NotBlankStr | None = None

        sorted_entries = sorted(entries, key=attrgetter("category"))
        groups = groupby(sorted_entries, key=attrgetter("category"))

        for category, group_iter in groups:
            group = list(group_iter)
            if len(group) < self._group_threshold:
                continue

            _, to_remove = self._select_entries(group)
            summary_content = self._build_summary(category, to_remove)

            store_request = MemoryStoreRequest(
                category=category,
                content=summary_content,
                metadata=MemoryMetadata(
                    source="consolidation",
                    tags=("consolidated",),
                ),
            )
            new_id = await self._backend.store(agent_id, store_request)
            if summary_id is None:
                summary_id = new_id

            for entry in to_remove:
                await self._backend.delete(agent_id, entry.id)
                removed_ids.append(entry.id)

        result = ConsolidationResult(
            removed_ids=tuple(removed_ids),
            summary_id=summary_id,
        )

        logger.info(
            STRATEGY_COMPLETE,
            agent_id=agent_id,
            consolidated_count=result.consolidated_count,
            summary_id=result.summary_id,
        )

        return result

    def _select_entries(
        self,
        group: list[MemoryEntry],
    ) -> tuple[MemoryEntry, list[MemoryEntry]]:
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
        to_remove = [e for e in group if e.id != best.id]
        return best, to_remove

    def _build_summary(
        self,
        category: MemoryCategory,
        entries: list[MemoryEntry],
    ) -> str:
        """Build a summary text from removed entries.

        Args:
            category: The memory category.
            entries: Entries being consolidated.

        Returns:
            Summary text combining key content.
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
