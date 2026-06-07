"""Procedural memory content extractor.

Queries the memory backend for procedural-category entries from
source agents and converts them to training items.
"""

import asyncio
from typing import TYPE_CHECKING, Final

from synthorg.core.enums import MemoryCategory
from synthorg.hr.training.models import ContentType, TrainingItem
from synthorg.memory.models import MemoryQuery
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.training import (
    HR_TRAINING_EXTRACTION_FAILED,
    HR_TRAINING_ITEMS_EXTRACTED,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.seniority import SeniorityLevel
    from synthorg.memory.models import MemoryEntry
    from synthorg.memory.protocol import MemoryBackend

logger = get_logger(__name__)

_MAX_ENTRIES_PER_AGENT: Final[int] = 100


class ProceduralMemoryExtractor:
    """Extract procedural memory entries from senior agents.

    Queries the memory backend for ``PROCEDURAL`` category entries
    from each source agent and converts them to ``TrainingItem``
    instances.

    Args:
        backend: Memory backend for retrieval.
    """

    def __init__(self, *, backend: MemoryBackend) -> None:
        self._backend = backend

    @property
    def content_type(self) -> ContentType:
        """The content type this extractor produces."""
        return ContentType.PROCEDURAL

    async def extract(
        self,
        *,
        source_agent_ids: tuple[NotBlankStr, ...],
        new_agent_role: NotBlankStr,  # noqa: ARG002
        new_agent_level: SeniorityLevel,  # noqa: ARG002
    ) -> tuple[TrainingItem, ...]:
        """Extract procedural memories from source agents in parallel.

        Args:
            source_agent_ids: Senior agents to extract from.
            new_agent_role: Role of the new hire (unused).
            new_agent_level: Seniority level (unused).

        Returns:
            Unranked procedural training items.
        """
        if not source_agent_ids:
            return ()

        query = MemoryQuery(
            categories=frozenset({MemoryCategory.PROCEDURAL}),
            limit=_MAX_ENTRIES_PER_AGENT,
        )

        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(self._retrieve_for_agent(agent_id, query))
                for agent_id in source_agent_ids
            ]

        items: list[TrainingItem] = []
        for task in tasks:
            agent_id, entries = task.result()
            items.extend(
                TrainingItem(
                    source_agent_id=str(agent_id),
                    content_type=ContentType.PROCEDURAL,
                    content=str(entry.content),
                    source_memory_id=str(entry.id),
                    metadata_tags=entry.metadata.tags,
                    created_at=entry.created_at,
                )
                for entry in entries
            )

        logger.debug(
            HR_TRAINING_ITEMS_EXTRACTED,
            content_type="procedural",
            agent_count=len(source_agent_ids),
            item_count=len(items),
        )
        return tuple(items)

    async def _retrieve_for_agent(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[NotBlankStr, tuple[MemoryEntry, ...]]:
        """Retrieve procedural entries for a single agent with error logging.

        Returns:
            Tuple ``(NotBlankStr, tuple[MemoryEntry, ...])``.

        Raises:
            Exception: Raised when the relevant invariant fails.
        """
        try:
            entries = await self._backend.retrieve(agent_id, query)
        except Exception as exc:
            logger.warning(
                HR_TRAINING_EXTRACTION_FAILED,
                content_type="procedural",
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        return agent_id, tuple(entries)
