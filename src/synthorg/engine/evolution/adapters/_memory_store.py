"""Shared memory storage helper for adaptation adapters."""

import json
from typing import TYPE_CHECKING

from synthorg.core.enums import MemoryCategory
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evolution import EVOLUTION_ADAPTATION_FAILED

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.evolution.models import AdaptationProposal
    from synthorg.memory.protocol import MemoryBackend

logger = get_logger(__name__)


async def store_proposal_as_memory(
    memory_backend: MemoryBackend,
    proposal: AdaptationProposal,
    agent_id: NotBlankStr,
    tag: str,
) -> None:
    """Store an adaptation proposal as a procedural memory entry.

    Args:
        memory_backend: Memory storage backend.
        proposal: The adaptation proposal to store.
        agent_id: Target agent.
        tag: Tag to apply to the memory entry (e.g. "evolution-prompt-injection").

    Raises:
        Exception: If the memory store operation fails.
    """
    try:
        content_parts = [proposal.description]
        if proposal.changes:
            content_parts.append(
                "Changes: " + json.dumps(proposal.changes, indent=2),
            )
        content: NotBlankStr = "\n".join(content_parts)

        request = MemoryStoreRequest(
            category=MemoryCategory.PROCEDURAL,
            namespace="default",
            content=content,
            metadata=MemoryMetadata(
                source=str(proposal.id),
                confidence=proposal.confidence,
                tags=(tag,),
            ),
        )

        await memory_backend.store(agent_id, request)
    except Exception as exc:
        logger.warning(
            EVOLUTION_ADAPTATION_FAILED,
            agent_id=agent_id,
            proposal_id=str(proposal.id),
            axis=proposal.axis.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
