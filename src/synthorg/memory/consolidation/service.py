"""Memory consolidation service.

Orchestrates retention cleanup, consolidation, archival, and
max-memories enforcement into a single maintenance entry point.
"""

from collections.abc import Mapping  # noqa: TC003
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.enums import MemoryCategory  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.consolidation.archival import ArchivalStore  # noqa: TC001
from synthorg.memory.consolidation.config import ConsolidationConfig  # noqa: TC001
from synthorg.memory.consolidation.models import (
    ArchivalEntry,
    ArchivalIndexEntry,
    ArchivalMode,
    ArchivalModeAssignment,
    ConsolidationResult,
)
from synthorg.memory.consolidation.retention import RetentionEnforcer
from synthorg.memory.consolidation.strategy import (
    ConsolidationStrategy,  # noqa: TC001
)
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.memory.protocol import MemoryBackend  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.settings.kill_switch import resolve_bool_with_fallback

if TYPE_CHECKING:
    from synthorg.settings.resolver import ConfigResolver
from synthorg.observability.events.consolidation import (
    ARCHIVAL_ENTRY_STORED,
    ARCHIVAL_FAILED,
    ARCHIVAL_INDEX_BUILT,
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
_MAX_ENFORCE_BATCH_MIN = 100
_MAX_ENFORCE_BATCH_MAX = 10_000
# ``MemoryQuery.limit`` has its own schema bound (``le=1000``); the
# max-enforce batch settings allows up to 10k records, so each
# per-batch fetch has to be clamped to the query-layer bound before
# constructing the MemoryQuery below. The enforce loop just issues
# more MemoryQuery fetches to cover any excess.
_MEMORY_QUERY_MAX_LIMIT = 1000


class MemoryConsolidationService:
    """Orchestrates memory consolidation, retention, and archival.

    Args:
        backend: Memory backend for CRUD operations.
        config: Consolidation configuration.
        strategy: Optional consolidation strategy (skips consolidation
            step if ``None``).
        archival_store: Optional archival store (skips archival if
            ``None`` or disabled in config).
        max_enforce_batch: Maximum memories to enforce retention on per
            invocation. Must match the bounds of the
            ``memory.consolidation_enforce_batch_size`` setting
            (``MemoryBridgeConfig``): between 100 and 10000 inclusive.
            Defaults to the module constant so the service still works
            standalone when the settings layer is not wired in.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        backend: MemoryBackend,
        config: ConsolidationConfig,
        strategy: ConsolidationStrategy | None = None,
        archival_store: ArchivalStore | None = None,
        max_enforce_batch: int = _MAX_ENFORCE_BATCH,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        if not (_MAX_ENFORCE_BATCH_MIN <= max_enforce_batch <= _MAX_ENFORCE_BATCH_MAX):
            msg = (
                "max_enforce_batch must be between "
                f"{_MAX_ENFORCE_BATCH_MIN} and {_MAX_ENFORCE_BATCH_MAX}, "
                f"got {max_enforce_batch}"
            )
            raise ValueError(msg)
        self._backend = backend
        self._config = config
        self._strategy = strategy
        self._archival_store = archival_store
        self._max_enforce_batch = max_enforce_batch
        # Resolver gate for the runtime ``memory.consolidation_enabled``
        # kill switch.  When wired, ``run_consolidation`` reads the
        # current value per cycle so an operator can pause without
        # restart; when None (test harness, standalone construction),
        # we fall back to the YAML-baked ``config.enabled`` field.
        self._config_resolver = config_resolver
        self._retention = RetentionEnforcer(
            config=config.retention,
            backend=backend,
        )

    async def run_consolidation(
        self,
        agent_id: NotBlankStr,
    ) -> ConsolidationResult:
        """Run memory consolidation for an agent.

        Retrieves up to 1000 entries per invocation and applies the
        consolidation strategy, then archives removed entries if archival
        is configured and enabled.  Per-entry archival failures are
        logged and skipped -- they do not abort the entire batch.

        Gated by the ``memory.consolidation_enabled`` kill-switch:
        when ``False`` the call short-circuits immediately without
        touching the backend or strategy, so operators can pause
        consolidation mid-flight without tearing down scheduling.
        The flag is resolved per call when a ``ConfigResolver`` is
        wired (runtime-controllable); without a resolver we fall back
        to the YAML-baked ``config.enabled`` field.

        Args:
            agent_id: Agent whose memories to consolidate.

        Returns:
            Consolidation result (including archival count).
        """
        enabled = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace="memory",
            key="consolidation_enabled",
            fallback=self._config.enabled,
        )
        if not enabled:
            logger.info(
                CONSOLIDATION_SKIPPED,
                agent_id=agent_id,
                reason="disabled_by_setting",
            )
            return ConsolidationResult()
        return await self._run_consolidation_unchecked(agent_id)

    async def _run_consolidation_unchecked(
        self,
        agent_id: NotBlankStr,
    ) -> ConsolidationResult:
        """Run consolidation without re-checking the kill-switch.

        Internal helper called by ``run_consolidation`` (after the gate
        passes) and by ``run_maintenance`` (which has already evaluated
        the gate once for the whole maintenance cycle).  Keeping this
        helper private prevents external callers from bypassing the
        ``memory.consolidation_enabled`` setting while still letting
        the maintenance flow resolve the flag exactly once.
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
                archived, index = await self._archive_entries(
                    agent_id,
                    entries,
                    result.removed_ids,
                    result.mode_assignments,
                )
                result = ConsolidationResult(
                    removed_ids=result.removed_ids,
                    summary_ids=result.summary_ids,
                    archived_count=archived,
                    mode_assignments=result.mode_assignments,
                    archival_index=index,
                )
        except Exception as exc:
            logger.warning(
                CONSOLIDATION_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
        ordering -- typically oldest-first, but consult the concrete
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
                batch_size = min(remaining, self._max_enforce_batch)
                # Cap the per-fetch size to MemoryQuery's own upper
                # bound; ``self._max_enforce_batch`` can exceed it
                # (up to 10k) and the query would then fail
                # validation before hitting the backend.
                effective_limit = min(batch_size, _MEMORY_QUERY_MAX_LIMIT)
                query = MemoryQuery(limit=effective_limit)
                entries = await self._backend.retrieve(agent_id, query)
                if not entries:
                    break
                for entry in entries:
                    if await self._backend.delete(agent_id, entry.id):
                        deleted += 1
                remaining -= len(entries)
        except Exception as exc:
            logger.warning(
                MAX_MEMORIES_ENFORCE_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
        *,
        agent_category_overrides: Mapping[MemoryCategory, int] | None = None,
        agent_default_retention_days: int | None = None,
    ) -> int:
        """Run retention cleanup for an agent.

        Args:
            agent_id: Agent whose expired memories to clean up.
            agent_category_overrides: Per-category retention overrides
                for this agent.
            agent_default_retention_days: Agent-level default retention
                in days.

        Returns:
            Number of expired memories deleted.
        """
        return await self._retention.cleanup_expired(
            agent_id,
            agent_category_overrides=agent_category_overrides,
            agent_default_retention_days=agent_default_retention_days,
        )

    async def run_maintenance(
        self,
        agent_id: NotBlankStr,
        *,
        agent_category_overrides: Mapping[MemoryCategory, int] | None = None,
        agent_default_retention_days: int | None = None,
    ) -> ConsolidationResult:
        """Run full maintenance cycle for an agent.

        Orchestrates: retention cleanup -> consolidation -> max enforcement.
        Gated by the ``memory.consolidation_enabled`` kill-switch: when
        ``False`` the whole cycle short-circuits (retention, consolidation,
        and max-memory enforcement all pause together so the scheduler can
        be resumed without partial state).

        Args:
            agent_id: Agent to maintain.
            agent_category_overrides: Per-category retention overrides
                for this agent.
            agent_default_retention_days: Agent-level default retention
                in days.

        Returns:
            Consolidation result from the consolidation step.
        """
        enabled = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace="memory",
            key="consolidation_enabled",
            fallback=self._config.enabled,
        )
        if not enabled:
            logger.info(
                CONSOLIDATION_SKIPPED,
                agent_id=agent_id,
                reason="disabled_by_setting",
                scope="maintenance",
            )
            return ConsolidationResult()

        logger.info(MAINTENANCE_START, agent_id=agent_id)
        try:
            await self.cleanup_retention(
                agent_id,
                agent_category_overrides=agent_category_overrides,
                agent_default_retention_days=agent_default_retention_days,
            )
            result = await self._run_consolidation_unchecked(agent_id)
            await self.enforce_max_memories(agent_id)
        except Exception as exc:
            logger.warning(
                MAINTENANCE_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
        mode_assignments: tuple[ArchivalModeAssignment, ...] = (),
    ) -> tuple[int, tuple[ArchivalIndexEntry, ...]]:
        """Archive removed entries to cold storage.

        Per-entry failures are logged at WARNING and skipped so that a
        single archival error does not abort the entire batch.  This is
        consistent with ``RetentionEnforcer``'s per-category isolation.

        Args:
            agent_id: Agent identifier.
            all_entries: All retrieved entries (to find removed ones).
            removed_ids: IDs of entries that were removed.
            mode_assignments: Per-entry archival mode assignments from
                the strategy (empty for strategies without dual-mode).

        Returns:
            Tuple of (archived count, archival index entries).
        """
        if self._archival_store is None:
            return 0, ()

        mode_map: dict[NotBlankStr, ArchivalMode] = {
            a.original_id: a.mode for a in mode_assignments
        }
        entry_map = {entry.id: entry for entry in all_entries}
        now = datetime.now(UTC)
        archived = 0
        index_entries: list[ArchivalIndexEntry] = []

        for removed_id in removed_ids:
            entry = entry_map.get(removed_id)
            if entry is None:
                logger.warning(
                    ARCHIVAL_FAILED,
                    original_id=removed_id,
                    agent_id=agent_id,
                    error="removed_id not found in retrieved entries",
                    error_type="KeyError",
                )
                continue
            idx = await self._archive_single_entry(
                entry,
                agent_id,
                mode_map,
                now,
            )
            if idx is not None:
                archived += 1
                index_entries.append(idx)

        index = tuple(index_entries)
        if index:
            logger.debug(
                ARCHIVAL_INDEX_BUILT,
                agent_id=agent_id,
                index_size=len(index),
            )

        return archived, index

    async def _archive_single_entry(
        self,
        entry: MemoryEntry,
        agent_id: NotBlankStr,
        mode_map: dict[NotBlankStr, ArchivalMode],
        now: datetime,
    ) -> ArchivalIndexEntry | None:
        """Archive a single entry to cold storage.

        Args:
            entry: Memory entry to archive.
            agent_id: Agent identifier.
            mode_map: Mapping of original IDs to archival modes.
            now: Current timestamp for archival.

        Returns:
            Index entry on success, ``None`` on failure.
        """
        assert self._archival_store is not None  # noqa: S101
        archival_mode = mode_map.get(entry.id, ArchivalMode.EXTRACTIVE)
        archival_entry = ArchivalEntry(
            original_id=entry.id,
            agent_id=entry.agent_id,
            content=entry.content,
            category=entry.category,
            metadata=entry.metadata,
            created_at=entry.created_at,
            archived_at=now,
            archival_mode=archival_mode,
        )
        try:
            archival_id = await self._archival_store.archive(archival_entry)
        except Exception as exc:
            logger.warning(
                ARCHIVAL_FAILED,
                original_id=entry.id,
                agent_id=agent_id,
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
            )
            return None
        logger.debug(
            ARCHIVAL_ENTRY_STORED,
            original_id=entry.id,
            agent_id=agent_id,
            archival_mode=archival_mode,
        )
        return ArchivalIndexEntry(
            original_id=entry.id,
            archival_id=archival_id,
            mode=archival_mode,
        )
