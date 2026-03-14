"""Memory consolidation service.

Orchestrates retention cleanup, consolidation, archival, and
max-memories enforcement into a single maintenance entry point.
"""

from datetime import UTC, datetime

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.consolidation.archival import ArchivalStore  # noqa: TC001
from synthorg.memory.consolidation.config import ConsolidationConfig  # noqa: TC001
from synthorg.memory.consolidation.models import ArchivalEntry, ConsolidationResult
from synthorg.memory.consolidation.retention import RetentionEnforcer
from synthorg.memory.consolidation.strategy import (
    ConsolidationStrategy,  # noqa: TC001
)
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.memory.protocol import MemoryBackend  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.consolidation import (
    ARCHIVAL_ENTRY_STORED,
    ARCHIVAL_FAILED,
    CONSOLIDATION_COMPLETE,
    CONSOLIDATION_FAILED,
    CONSOLIDATION_SKIPPED,
    CONSOLIDATION_START,
    MAINTENANCE_COMPLETE,
    MAINTENANCE_FAILED,
    MAINTENANCE_START,
    MAX_MEMORIES_ENFORCE_FAILED,
    MAX_MEMORIES_ENFORCED,
)

logger = get_logger(__name__)

_MAX_ENFORCE_BATCH = 1000


class MemoryConsolidationService:
    """Orchestrates memory consolidation, retention, and archival.

    Args:
        backend: Memory backend for CRUD operations.
        config: Consolidation configuration.
        strategy: Optional consolidation strategy (skips consolidation
            step if ``None``).
        archival_store: Optional archival store (skips archival if
            ``None`` or disabled in config).
    """

    def __init__(
        self,
        *,
        backend: MemoryBackend,
        config: ConsolidationConfig,
        strategy: ConsolidationStrategy | None = None,
        archival_store: ArchivalStore | None = None,
    ) -> None:
        self._backend = backend
        self._config = config
        self._strategy = strategy
        self._archival_store = archival_store
        self._retention = RetentionEnforcer(
            config=config.retention,
            backend=backend,
        )

    async def run_consolidation(
        self,
        agent_id: NotBlankStr,
    ) -> ConsolidationResult:
        """Run memory consolidation for an agent.

        Retrieves memories, applies the consolidation strategy, and
        archives removed entries if archival is configured and enabled.
        Per-entry archival failures are logged and skipped — they do
        not abort the entire archival batch.

        Args:
            agent_id: Agent whose memories to consolidate.

        Returns:
            Consolidation result (including archival count).
        """
        if self._strategy is None:
            logger.info(CONSOLIDATION_SKIPPED, agent_id=agent_id)
            return ConsolidationResult()

        logger.info(CONSOLIDATION_START, agent_id=agent_id)

        try:
            query = MemoryQuery(limit=1000)
            entries = await self._backend.retrieve(agent_id, query)

            result = await self._strategy.consolidate(
                entries,
                agent_id=agent_id,
            )

            if self._archival_store is not None and self._config.archival.enabled:
                archived = await self._archive_entries(
                    agent_id,
                    entries,
                    result.removed_ids,
                )
                result = ConsolidationResult(
                    removed_ids=result.removed_ids,
                    summary_id=result.summary_id,
                    archived_count=archived,
                )
        except Exception as exc:
            logger.exception(
                CONSOLIDATION_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
        else:
            logger.info(
                CONSOLIDATION_COMPLETE,
                agent_id=agent_id,
                consolidated_count=result.consolidated_count,
                archived_count=result.archived_count,
            )
            return result

    async def enforce_max_memories(
        self,
        agent_id: NotBlankStr,
    ) -> int:
        """Enforce the maximum memories limit for an agent.

        Retrieves excess entries in batches (up to 1000 per query,
        the ``MemoryQuery.limit`` cap) and deletes them.  The entries
        selected for deletion depend on the backend's default query
        ordering — typically oldest-first, but consult the concrete
        backend for specifics.

        Args:
            agent_id: Agent to check.

        Returns:
            Number of entries deleted.
        """
        try:
            total = await self._backend.count(agent_id)
            excess = total - self._config.max_memories_per_agent

            if excess <= 0:
                return 0

            deleted = 0
            remaining = excess
            while remaining > 0:
                batch_size = min(remaining, _MAX_ENFORCE_BATCH)
                query = MemoryQuery(limit=batch_size)
                entries = await self._backend.retrieve(agent_id, query)
                if not entries:
                    break
                for entry in entries:
                    if await self._backend.delete(agent_id, entry.id):
                        deleted += 1
                remaining -= len(entries)
        except Exception as exc:
            logger.exception(
                MAX_MEMORIES_ENFORCE_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
        else:
            logger.info(
                MAX_MEMORIES_ENFORCED,
                agent_id=agent_id,
                total_before=total,
                deleted=deleted,
            )
            return deleted

    async def cleanup_retention(
        self,
        agent_id: NotBlankStr,
    ) -> int:
        """Run retention cleanup for an agent.

        Args:
            agent_id: Agent whose expired memories to clean up.

        Returns:
            Number of expired memories deleted.
        """
        return await self._retention.cleanup_expired(agent_id)

    async def run_maintenance(
        self,
        agent_id: NotBlankStr,
    ) -> ConsolidationResult:
        """Run full maintenance cycle for an agent.

        Orchestrates: retention cleanup → consolidation → max enforcement.

        Args:
            agent_id: Agent to maintain.

        Returns:
            Consolidation result from the consolidation step.
        """
        logger.info(MAINTENANCE_START, agent_id=agent_id)
        try:
            await self.cleanup_retention(agent_id)
            result = await self.run_consolidation(agent_id)
            await self.enforce_max_memories(agent_id)
        except Exception as exc:
            logger.exception(
                MAINTENANCE_FAILED,
                agent_id=agent_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise
        else:
            logger.info(MAINTENANCE_COMPLETE, agent_id=agent_id)
            return result

    async def _archive_entries(
        self,
        agent_id: NotBlankStr,
        all_entries: tuple[MemoryEntry, ...],
        removed_ids: tuple[NotBlankStr, ...],
    ) -> int:
        """Archive removed entries to cold storage.

        Per-entry failures are logged at WARNING and skipped so that a
        single archival error does not abort the entire batch.  This is
        consistent with ``RetentionEnforcer``'s per-category isolation.

        Args:
            agent_id: Agent identifier.
            all_entries: All retrieved entries (to find removed ones).
            removed_ids: IDs of entries that were removed.

        Returns:
            Number of entries successfully archived.
        """
        if self._archival_store is None:
            return 0
        removed_set = set(removed_ids)
        now = datetime.now(UTC)
        archived = 0

        for entry in all_entries:
            if entry.id not in removed_set:
                continue

            archival_entry = ArchivalEntry(
                original_id=entry.id,
                agent_id=entry.agent_id,
                content=entry.content,
                category=entry.category,
                metadata=entry.metadata,
                created_at=entry.created_at,
                archived_at=now,
            )
            try:
                await self._archival_store.archive(archival_entry)
            except Exception as exc:
                logger.warning(
                    ARCHIVAL_FAILED,
                    original_id=entry.id,
                    agent_id=agent_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                continue
            archived += 1
            logger.debug(
                ARCHIVAL_ENTRY_STORED,
                original_id=entry.id,
                agent_id=agent_id,
            )

        return archived
