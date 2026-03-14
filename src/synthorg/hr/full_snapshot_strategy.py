"""Full-snapshot memory archival strategy (D10 initial).

Archives all agent memories to cold storage, promotes SEMANTIC
and PROCEDURAL entries to org memory, then cleans the hot store.
"""

from datetime import UTC, datetime

from pydantic import ValidationError

from synthorg.core.enums import (
    MemoryCategory,
    OrgFactCategory,
    SeniorityLevel,
)
from synthorg.core.types import NotBlankStr
from synthorg.hr.archival_protocol import ArchivalResult
from synthorg.hr.errors import MemoryArchivalError
from synthorg.memory.consolidation.archival import ArchivalStore  # noqa: TC001
from synthorg.memory.consolidation.models import ArchivalEntry
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.memory.org.models import OrgFactAuthor, OrgFactWriteRequest
from synthorg.memory.org.protocol import OrgMemoryBackend  # noqa: TC001
from synthorg.memory.protocol import MemoryBackend  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.hr import (
    HR_ARCHIVAL_ENTRY_FAILED,
    HR_FIRING_MEMORY_ARCHIVED,
)

logger = get_logger(__name__)

# Categories eligible for org memory promotion.
_PROMOTABLE_CATEGORIES: frozenset[MemoryCategory] = frozenset(
    {
        MemoryCategory.SEMANTIC,
        MemoryCategory.PROCEDURAL,
    }
)

# Map memory categories to org fact categories for promotion.
_CATEGORY_MAP: dict[MemoryCategory, OrgFactCategory] = {
    MemoryCategory.SEMANTIC: OrgFactCategory.CONVENTION,
    MemoryCategory.PROCEDURAL: OrgFactCategory.PROCEDURE,
}

# Maximum memories to retrieve per archival operation.
_MAX_MEMORIES_PER_ARCHIVAL: int = 1000


class FullSnapshotStrategy:
    """Archive all agent memories with org memory promotion.

    Pipeline:
        1. Retrieve all memories from the hot store.
        2. Archive each to cold storage.
        3. Promote SEMANTIC and PROCEDURAL entries to org memory.
        4. Delete from hot store.
        5. Return archival result.

    Per-entry errors are logged and skipped (partial success).
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "full_snapshot"

    async def archive(
        self,
        *,
        agent_id: NotBlankStr,
        memory_backend: MemoryBackend,
        archival_store: ArchivalStore,
        org_memory_backend: OrgMemoryBackend | None = None,
        agent_seniority: SeniorityLevel | None = None,
    ) -> ArchivalResult:
        """Archive all memories for a departing agent.

        Args:
            agent_id: Agent whose memories to archive.
            memory_backend: Hot memory store.
            archival_store: Cold archival storage.
            org_memory_backend: Optional org memory for promotion.
            agent_seniority: Seniority level of the departing agent.
                Required for org memory promotion (skipped if None).

        Returns:
            Result of the archival operation.

        Raises:
            MemoryArchivalError: If retrieval from hot store fails.
        """
        try:
            entries = await memory_backend.retrieve(
                agent_id,
                MemoryQuery(limit=_MAX_MEMORIES_PER_ARCHIVAL),
            )
        except (OSError, ValueError, MemoryArchivalError) as exc:
            msg = f"Failed to retrieve memories for agent {agent_id!r}"
            logger.error(  # noqa: TRY400
                HR_ARCHIVAL_ENTRY_FAILED,
                agent_id=agent_id,
                phase="retrieve",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise MemoryArchivalError(msg) from exc

        now = datetime.now(UTC)

        archived_count, deleted_ids = await self._archive_entries(
            entries, archival_store, agent_id, now
        )

        promoted_count = await self._promote_to_org(
            entries, org_memory_backend, agent_id, agent_seniority
        )

        hot_store_cleaned = await self._clean_hot_store(
            memory_backend, agent_id, deleted_ids
        )

        result = ArchivalResult(
            agent_id=agent_id,
            total_archived=archived_count,
            promoted_to_org=promoted_count,
            hot_store_cleaned=hot_store_cleaned,
            strategy_name=NotBlankStr(self.name),
        )

        logger.info(
            HR_FIRING_MEMORY_ARCHIVED,
            agent_id=agent_id,
            archived=archived_count,
            promoted=promoted_count,
            cleaned=hot_store_cleaned,
        )
        return result

    async def _archive_entries(
        self,
        entries: tuple[MemoryEntry, ...],
        archival_store: ArchivalStore,
        agent_id: NotBlankStr,
        now: datetime,
    ) -> tuple[int, list[str]]:
        """Archive memory entries to cold storage.

        Args:
            entries: Memory entries to archive.
            archival_store: Cold archival storage.
            agent_id: The departing agent's ID.
            now: Timestamp for archival records.

        Returns:
            Tuple of (archived count, list of deleted IDs).
        """
        archived_count = 0
        deleted_ids: list[str] = []

        for entry in entries:
            try:
                archival_entry = ArchivalEntry(
                    original_id=entry.id,
                    agent_id=agent_id,
                    content=NotBlankStr(entry.content),
                    category=entry.category,
                    metadata=entry.metadata,
                    created_at=entry.created_at,
                    archived_at=now,
                )
                await archival_store.archive(archival_entry)
                archived_count += 1
                deleted_ids.append(str(entry.id))
            except (OSError, ValueError, ValidationError) as exc:
                logger.warning(
                    HR_ARCHIVAL_ENTRY_FAILED,
                    agent_id=agent_id,
                    memory_id=str(entry.id),
                    phase="archive",
                    error=str(exc),
                )
                continue

        return archived_count, deleted_ids

    async def _promote_to_org(
        self,
        entries: tuple[MemoryEntry, ...],
        org_memory_backend: OrgMemoryBackend | None,
        agent_id: NotBlankStr,
        agent_seniority: SeniorityLevel | None,
    ) -> int:
        """Promote eligible memories to org memory.

        Skipped entirely if no org backend or seniority is provided.

        Args:
            entries: Memory entries to consider for promotion.
            org_memory_backend: Org memory backend.
            agent_id: The departing agent's ID.
            agent_seniority: Agent seniority for authorship.

        Returns:
            Number of entries promoted.
        """
        if org_memory_backend is None or agent_seniority is None:
            return 0

        promoted_count = 0
        for entry in entries:
            if entry.category not in _PROMOTABLE_CATEGORIES:
                continue
            try:
                org_category = _CATEGORY_MAP[entry.category]
                author = OrgFactAuthor(
                    agent_id=agent_id,
                    seniority=agent_seniority,
                )
                write_req = OrgFactWriteRequest(
                    content=NotBlankStr(entry.content),
                    category=org_category,
                )
                await org_memory_backend.write(write_req, author=author)
                promoted_count += 1
            except (OSError, ValueError, KeyError) as exc:
                logger.warning(
                    HR_ARCHIVAL_ENTRY_FAILED,
                    agent_id=agent_id,
                    memory_id=str(entry.id),
                    phase="promote",
                    error=str(exc),
                )

        return promoted_count

    async def _clean_hot_store(
        self,
        memory_backend: MemoryBackend,
        agent_id: NotBlankStr,
        deleted_ids: list[str],
    ) -> bool:
        """Delete archived entries from the hot store.

        Args:
            memory_backend: Hot memory store.
            agent_id: The departing agent's ID.
            deleted_ids: IDs of entries to delete.

        Returns:
            Whether all deletions succeeded.
        """
        hot_store_cleaned = True
        for memory_id in deleted_ids:
            try:
                await memory_backend.delete(agent_id, NotBlankStr(memory_id))
            except (OSError, ValueError) as exc:
                hot_store_cleaned = False
                logger.warning(
                    HR_ARCHIVAL_ENTRY_FAILED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    phase="delete",
                    error=str(exc),
                )
        return hot_store_cleaned
