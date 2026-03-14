"""SharedKnowledgeStore operations for the Mem0 backend.

Standalone async functions that implement the ``SharedKnowledgeStore``
protocol methods.  The ``Mem0MemoryBackend`` class delegates to these
functions after performing connection and agent-ID validation.

Separated from ``adapter.py`` to keep individual modules under the
800-line guideline while maintaining a single cohesive backend package.
"""

import asyncio
import builtins
from typing import TYPE_CHECKING, Any

from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.mem0.mappers import (
    PUBLISHER_KEY,
    SHARED_NAMESPACE,
    apply_post_filters,
    build_mem0_metadata,
    extract_publisher,
    mem0_result_to_entry,
    resolve_publisher,
    validate_add_result,
    validate_mem0_result,
)
from synthorg.memory.errors import (
    MemoryRetrievalError,
    MemoryStoreError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_BACKEND_AGENT_ID_REJECTED,
    MEMORY_BACKEND_SYSTEM_ERROR,
    MEMORY_SHARED_PUBLISH_FAILED,
    MEMORY_SHARED_PUBLISHED,
    MEMORY_SHARED_RETRACT_FAILED,
    MEMORY_SHARED_RETRACTED,
    MEMORY_SHARED_SEARCH_FAILED,
    MEMORY_SHARED_SEARCHED,
)

if TYPE_CHECKING:
    from synthorg.memory.backends.mem0.adapter import Mem0Client
    from synthorg.memory.models import MemoryEntry, MemoryQuery, MemoryStoreRequest


logger = get_logger(__name__)


def _check_retract_ownership(
    raw: dict[str, Any],
    agent_id: NotBlankStr,
    memory_id: NotBlankStr,
) -> None:
    """Verify the caller published this shared memory entry.

    Raises:
        MemoryStoreError: If ownership cannot be verified.
    """
    owner_ns = raw.get("user_id")
    if owner_ns is None:
        logger.warning(
            MEMORY_SHARED_RETRACT_FAILED,
            agent_id=agent_id,
            memory_id=memory_id,
            reason="unverifiable_ownership",
        )
        msg = (
            f"Memory {memory_id} has no user_id — "
            f"ownership unverifiable, refusing retraction"
        )
        raise MemoryStoreError(msg)
    if owner_ns != SHARED_NAMESPACE:
        logger.warning(
            MEMORY_SHARED_RETRACT_FAILED,
            agent_id=agent_id,
            memory_id=memory_id,
            reason="not in shared namespace",
            actual_namespace=str(owner_ns),
        )
        msg = (
            f"Memory {memory_id} is not in the shared namespace — "
            f"use delete() to remove private entries"
        )
        raise MemoryStoreError(msg)

    publisher = extract_publisher(raw)
    if publisher is None:
        logger.warning(
            MEMORY_SHARED_RETRACT_FAILED,
            agent_id=agent_id,
            memory_id=memory_id,
            reason="not a shared memory entry (no publisher)",
        )
        msg = f"Memory {memory_id} is not a shared memory entry (no publisher metadata)"
        raise MemoryStoreError(msg)

    if publisher != str(agent_id):
        logger.warning(
            MEMORY_SHARED_RETRACT_FAILED,
            agent_id=agent_id,
            memory_id=memory_id,
            reason="ownership mismatch",
            publisher=publisher,
        )
        msg = (
            f"Agent {agent_id} cannot retract memory "
            f"{memory_id} published by {publisher}"
        )
        raise MemoryStoreError(msg)


async def publish_shared(
    client: Mem0Client,
    agent_id: NotBlankStr,
    request: MemoryStoreRequest,
) -> NotBlankStr:
    """Publish a memory to the shared knowledge store.

    Args:
        client: Connected Mem0 client.
        agent_id: Publishing agent identifier.
        request: Memory content and metadata.

    Returns:
        The backend-assigned shared memory ID.

    Raises:
        MemoryStoreError: If the publish operation fails.
    """
    try:
        metadata = {
            **build_mem0_metadata(request),
            PUBLISHER_KEY: str(agent_id),
        }
        kwargs = {
            "messages": [
                {"role": "user", "content": request.content},
            ],
            "user_id": SHARED_NAMESPACE,
            "metadata": metadata,
            "infer": False,
        }
        result = await asyncio.to_thread(client.add, **kwargs)
        memory_id = validate_add_result(result, context="shared publish")
    except MemoryStoreError as exc:
        logger.warning(
            MEMORY_SHARED_PUBLISH_FAILED,
            agent_id=agent_id,
            error=str(exc),
            error_type="MemoryStoreError",
        )
        raise
    except (builtins.MemoryError, RecursionError) as exc:
        logger.exception(
            MEMORY_BACKEND_SYSTEM_ERROR,
            operation="publish",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    except Exception as exc:
        logger.warning(
            MEMORY_SHARED_PUBLISH_FAILED,
            agent_id=agent_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        msg = f"Failed to publish shared memory: {exc}"
        raise MemoryStoreError(msg) from exc
    else:
        logger.info(
            MEMORY_SHARED_PUBLISHED,
            agent_id=agent_id,
            memory_id=memory_id,
        )
        return memory_id


async def search_shared_memories(
    client: Mem0Client,
    query: MemoryQuery,
    *,
    exclude_agent: NotBlankStr | None = None,
) -> tuple[MemoryEntry, ...]:
    """Search the shared knowledge store across agents.

    Args:
        client: Connected Mem0 client.
        query: Search parameters.
        exclude_agent: Optional agent ID to exclude from results.

    Returns:
        Matching shared memory entries ordered by relevance.

    Raises:
        MemoryRetrievalError: If the search fails.
    """
    if exclude_agent is not None and str(exclude_agent) == SHARED_NAMESPACE:
        msg = (
            "exclude_agent must not be the reserved shared namespace: "
            f"{SHARED_NAMESPACE!r}"
        )
        logger.warning(
            MEMORY_BACKEND_AGENT_ID_REJECTED,
            agent_id=exclude_agent,
            reason="reserved shared namespace used as exclude_agent",
        )
        raise MemoryRetrievalError(msg)
    try:
        if query.text is not None:
            raw_result = await asyncio.to_thread(
                client.search,
                query=str(query.text),
                user_id=SHARED_NAMESPACE,
                limit=query.limit,
            )
        else:
            raw_result = await asyncio.to_thread(
                client.get_all,
                user_id=SHARED_NAMESPACE,
                limit=query.limit,
            )
        raw_list = validate_mem0_result(
            raw_result,
            context="search_shared",
        )

        raw_entries = tuple(
            mem0_result_to_entry(
                item,
                NotBlankStr(
                    resolve_publisher(item),
                ),
            )
            for item in raw_list
        )
        filtered = apply_post_filters(raw_entries, query)

        if exclude_agent is not None:
            filtered = tuple(e for e in filtered if e.agent_id != exclude_agent)
    except MemoryRetrievalError as exc:
        logger.warning(
            MEMORY_SHARED_SEARCH_FAILED,
            error=str(exc),
            error_type="MemoryRetrievalError",
            query_text=query.text,
            exclude_agent=exclude_agent,
        )
        raise
    except (builtins.MemoryError, RecursionError) as exc:
        logger.exception(
            MEMORY_BACKEND_SYSTEM_ERROR,
            operation="search_shared",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    except Exception as exc:
        logger.warning(
            MEMORY_SHARED_SEARCH_FAILED,
            error=str(exc),
            error_type=type(exc).__name__,
            query_text=query.text,
            exclude_agent=exclude_agent,
        )
        msg = f"Failed to search shared knowledge: {exc}"
        raise MemoryRetrievalError(msg) from exc
    else:
        logger.info(
            MEMORY_SHARED_SEARCHED,
            count=len(filtered),
            exclude_agent=exclude_agent,
        )
        return filtered


async def retract_shared(
    client: Mem0Client,
    agent_id: NotBlankStr,
    memory_id: NotBlankStr,
) -> bool:
    """Remove a memory from the shared knowledge store.

    Verifies publisher ownership before deletion.

    Args:
        client: Connected Mem0 client.
        agent_id: Retracting agent identifier.
        memory_id: Shared memory identifier.

    Returns:
        ``True`` if retracted, ``False`` if not found.

    Raises:
        MemoryStoreError: If the retraction operation fails or
            ownership verification fails.
    """
    try:
        raw = await asyncio.to_thread(client.get, str(memory_id))
        if raw is None:
            logger.debug(
                MEMORY_SHARED_RETRACTED,
                agent_id=agent_id,
                memory_id=memory_id,
                found=False,
            )
            return False

        _check_retract_ownership(raw, agent_id, memory_id)
        await asyncio.to_thread(client.delete, str(memory_id))
    except MemoryStoreError:
        # Ownership-check MemoryStoreErrors are already logged
        # with context (reason, publisher) above — re-raise
        # without duplicate logging.
        raise
    except (builtins.MemoryError, RecursionError) as exc:
        logger.exception(
            MEMORY_BACKEND_SYSTEM_ERROR,
            operation="retract",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    except Exception as exc:
        logger.warning(
            MEMORY_SHARED_RETRACT_FAILED,
            agent_id=agent_id,
            memory_id=memory_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        msg = f"Failed to retract shared memory {memory_id}: {exc}"
        raise MemoryStoreError(msg) from exc
    else:
        logger.info(
            MEMORY_SHARED_RETRACTED,
            agent_id=agent_id,
            memory_id=memory_id,
            found=True,
        )
        return True
