# module-kind: adapter
"""Durable offload of compacted turn batches to the memory backend.

After a compaction archives the oldest turns into a summary, the full
detail is otherwise lost. This offloader persists the archived batch to
the memory backend as a PROCEDURAL entry tagged ``compaction:offloaded``,
so a resume / investigation path can re-hydrate the detail that the
in-context summary elided. Best-effort: an offload failure is logged and
never blocks the compaction itself.
"""

from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.context_budget import (
    CONTEXT_BUDGET_COMPACTION_OFFLOAD_FAILED,
    CONTEXT_BUDGET_COMPACTION_OFFLOAD_REHYDRATED,
    CONTEXT_BUDGET_COMPACTION_OFFLOAD_STORED,
)
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)

OFFLOAD_TAG: Final[NotBlankStr] = NotBlankStr("compaction:offloaded")
_OFFLOAD_SOURCE: Final[NotBlankStr] = NotBlankStr("compaction")
_DEFAULT_REHYDRATE_LIMIT: Final[int] = 20


class MemoryOffloader:
    """Persists and re-hydrates compacted turn batches via memory.

    Args:
        backend: Memory backend the archived batch is stored to.
    """

    def __init__(self, *, backend: MemoryBackend) -> None:
        self._backend = backend

    async def offload(
        self,
        *,
        agent_id: NotBlankStr,
        archivable: tuple[ChatMessage, ...],
        execution_id: str,
    ) -> None:
        """Store the archived batch as a tagged PROCEDURAL memory entry.

        Best-effort: a backend failure is logged and swallowed so
        compaction is never blocked by the offload.
        """
        content = _serialise_batch(archivable)
        if not content:
            return
        request = MemoryStoreRequest(
            category=MemoryCategory.PROCEDURAL,
            content=NotBlankStr(content),
            metadata=MemoryMetadata(
                source=_OFFLOAD_SOURCE,
                tags=(OFFLOAD_TAG, NotBlankStr(f"execution:{execution_id}")),
            ),
        )
        try:
            await self._backend.store(agent_id, request)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                CONTEXT_BUDGET_COMPACTION_OFFLOAD_FAILED,
                execution_id=execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        logger.info(
            CONTEXT_BUDGET_COMPACTION_OFFLOAD_STORED,
            execution_id=execution_id,
            archived_count=len(archivable),
        )

    async def rehydrate(
        self,
        *,
        agent_id: NotBlankStr,
        limit: int = _DEFAULT_REHYDRATE_LIMIT,
    ) -> tuple[MemoryEntry, ...]:
        """Return the agent's offloaded compaction batches, newest-first.

        Args:
            agent_id: Agent whose offloaded batches to retrieve.
            limit: Maximum entries to return.

        Returns:
            The offloaded PROCEDURAL memory entries.
        """
        entries = await self._backend.retrieve(
            agent_id,
            MemoryQuery(
                categories=frozenset({MemoryCategory.PROCEDURAL}),
                tags=(OFFLOAD_TAG,),
                limit=limit,
            ),
        )
        logger.debug(
            CONTEXT_BUDGET_COMPACTION_OFFLOAD_REHYDRATED,
            agent_id=str(agent_id),
            count=len(entries),
        )
        return entries


def _serialise_batch(messages: tuple[ChatMessage, ...]) -> str:
    """Join archived messages into a role-tagged transcript for storage.

    Returns:
        The transcript text, or an empty string when no message carries
        content.
    """
    lines = [
        f"{m.role.value}: {m.content.strip()}"
        for m in messages
        if m.content and m.content.strip()
    ]
    return "\n".join(lines)


__all__ = ["OFFLOAD_TAG", "MemoryOffloader"]
