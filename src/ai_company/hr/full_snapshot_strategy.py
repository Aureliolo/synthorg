"""Full-snapshot memory archival strategy (D10 initial).

Archives all agent memories to cold storage, promotes SEMANTIC
and PROCEDURAL entries to org memory, then cleans the hot store.
"""

from datetime import UTC, datetime

from ai_company.core.enums import MemoryCategory, OrgFactCategory
from ai_company.core.types import NotBlankStr
from ai_company.hr.archival_protocol import ArchivalResult
from ai_company.hr.errors import MemoryArchivalError
from ai_company.memory.consolidation.archival import ArchivalStore  # noqa: TC001
from ai_company.memory.consolidation.models import ArchivalEntry
from ai_company.memory.models import MemoryQuery
from ai_company.memory.org.models import OrgFactAuthor, OrgFactWriteRequest
from ai_company.memory.org.protocol import OrgMemoryBackend  # noqa: TC001
from ai_company.memory.protocol import MemoryBackend  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.hr import HR_FIRING_MEMORY_ARCHIVED

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
    ) -> ArchivalResult:
        """Archive all memories for a departing agent.

        Args:
            agent_id: Agent whose memories to archive.
            memory_backend: Hot memory store.
            archival_store: Cold archival storage.
            org_memory_backend: Optional org memory for promotion.

        Returns:
            Result of the archival operation.

        Raises:
            MemoryArchivalError: If retrieval from hot store fails.
        """
        try:
            entries = await memory_backend.retrieve(
                agent_id,
                MemoryQuery(limit=1000),
            )
        except Exception as exc:
            msg = f"Failed to retrieve memories for agent {agent_id!r}"
            logger.exception(
                HR_FIRING_MEMORY_ARCHIVED,
                agent_id=agent_id,
                error=str(exc),
            )
            raise MemoryArchivalError(msg) from exc

        now = datetime.now(UTC)
        archived_count = 0
        promoted_count = 0
        deleted_ids: list[str] = []

        for entry in entries:
            # Archive to cold storage.
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
            except Exception:
                logger.warning(
                    HR_FIRING_MEMORY_ARCHIVED,
                    agent_id=agent_id,
                    memory_id=str(entry.id),
                    error="archive_failed",
                )
                continue

            # Promote to org memory if eligible.
            if (
                org_memory_backend is not None
                and entry.category in _PROMOTABLE_CATEGORIES
            ):
                try:
                    org_category = _CATEGORY_MAP.get(
                        entry.category,
                        OrgFactCategory.CONVENTION,
                    )
                    author = OrgFactAuthor(agent_id=agent_id)
                    request = OrgFactWriteRequest(
                        content=NotBlankStr(entry.content),
                        category=org_category,
                    )
                    await org_memory_backend.write(request, author=author)
                    promoted_count += 1
                except Exception:
                    logger.warning(
                        HR_FIRING_MEMORY_ARCHIVED,
                        agent_id=agent_id,
                        memory_id=str(entry.id),
                        error="promote_failed",
                    )

        # Clean hot store.
        hot_store_cleaned = True
        for memory_id in deleted_ids:
            try:
                await memory_backend.delete(agent_id, NotBlankStr(memory_id))
            except Exception:
                hot_store_cleaned = False
                logger.warning(
                    HR_FIRING_MEMORY_ARCHIVED,
                    agent_id=agent_id,
                    memory_id=memory_id,
                    error="delete_failed",
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
